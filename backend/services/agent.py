"""LLM assistant loop: the model chooses tools, the app runs them, the model explains.

The LLM client is injectable (`LLMClient` protocol), so tests use a scripted fake and the
app uses Groq. The model only ever sees numbers that came back from tools.
"""
import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from weakref import WeakKeyDictionary

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import Player
from services.league import POSITIONS
from services.my_team import my_leagues
from services.projections import latest_season, latest_week
from tools.definitions import TOOL_ERRORS, TOOLS, sanitize_for_agent
from tools.registry import ToolError, ToolRegistry

log = logging.getLogger(__name__)

MAX_ROUNDS = 6
MAX_TOOL_RESULT_CHARS = 6_000   # Groq's free tier allows ~8k tokens per minute in total
SYSTEM_PROMPT = """You are a fantasy football analyst inside a FantasyPros-style app.

Rules:
- Get every number from a tool call. Never guess or invent stats, projections, or rankings.
- Only name players that a tool returned in this conversation (or the user named). Don't add
  players from memory: rosters, roles and availability change every week.
- Waiver targets come only from get_waiver_targets (its pct_rostered is how widely owned they are).
  Trade ideas come only from find_trades or evaluate_trade; present them as offers to propose,
  and say the verdict is this app's value, not a promise the other manager accepts.
- Label numbers by type: "actual" (box score), "expected" (nflverse usage model),
  "projection"/"trade value" (this app's model), or "ECR" (FantasyPros expert consensus).
- If a tool returns an error (unknown or ambiguous player, missing data), say so plainly or
  retry with a corrected name or id.
- Default to PPR, 12 teams, 1 QB unless the user says otherwise.
- Be concise: lead with the answer, then the key numbers behind it.
- Replies are shown as plain text in a chat bubble: use short paragraphs and "- " bullet
  lines. No markdown tables, headings, or **bold** markers.

Field guide (use these meanings; don't relabel fields):
- ppg = actual fantasy points per game; expected_ppg = usage-based expected points per game.
- ros_ppg = the app's rest-of-season projection; trade_value = projected rest-of-season points
  above a waiver-wire replacement; ecr = FantasyPros expert consensus rank.
- fpoe_per_game = ppg minus expected_ppg (negative = underperforming usage).
- this_week_proj, get_ros_ppg, ros_value = projections (this app's model), never "actual".
- get_playoff_schedule is already in words ("6% harder than average"); quote it as given.
- evaluate_trade: `give` = players the user sends, `get` = players the user receives. Verdicts
  "slight_win" / "big_win" mean the USER wins; "slight_loss" / "big_loss" mean the user loses.

Data available: NFL seasons through {season}, regular season through week {week}.
{leagues}"""


class LLMClient(Protocol):
    def complete(self, messages: list[dict], tools: list[dict] | None) -> dict:
        """Return {"content": str | None, "tool_calls": [{"id", "name", "arguments"}]}."""


class AgentUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolCallTrace:
    name: str
    arguments: Mapping[str, Any]
    ok: bool


@dataclass(frozen=True)
class AgentReply:
    reply: str
    tool_calls: tuple[ToolCallTrace, ...]


class ToolCallRejected(RuntimeError):
    """The provider refused the model's own tool call (e.g. arguments outside the schema)."""


def _rate_limited(exc: Exception) -> bool:
    body = getattr(exc, "body", None)
    error = body.get("error", body) if isinstance(body, dict) else None
    return (getattr(exc, "status_code", None) in (413, 429)
            or (isinstance(error, dict) and error.get("code") == "rate_limit_exceeded"))


def _rejected_tool_call(exc: Exception) -> str | None:
    """Groq answers a schema-violating tool call with HTTP 400 code 'tool_use_failed'."""
    body = getattr(exc, "body", None)
    error = body.get("error", body) if isinstance(body, dict) else None
    if isinstance(error, dict) and error.get("code") == "tool_use_failed":
        return str(error.get("message") or "the tool call did not match the tool's schema")
    return None


class GroqClient:
    def __init__(self, api_key: str, model: str):
        from groq import Groq   # imported lazily so the app runs without the package

        self._client = Groq(api_key=api_key)
        self._model = model

    def complete(self, messages: list[dict], tools: list[dict] | None) -> dict:
        options: dict[str, Any] = {"model": self._model, "messages": messages, "temperature": 0.2}
        if tools:
            options.update(tools=tools, tool_choice="auto")
        try:
            message = self._client.chat.completions.create(**options).choices[0].message
        except Exception as exc:  # groq raises several API/connection error types
            rejected = _rejected_tool_call(exc)
            if rejected:
                log.warning("Groq rejected a tool call: %s", rejected)
                raise ToolCallRejected(rejected) from exc
            if _rate_limited(exc):
                log.warning("Groq rate limit: %s", exc)
                raise AgentUnavailable("The AI provider's free-tier limit (about 8,000 tokens per minute) was "
                                       "reached. Wait a minute, or ask a narrower question.") from exc
            log.exception("Groq request failed")
            raise AgentUnavailable(f"The language model request failed: {type(exc).__name__}") from exc
        return {"content": message.content, "tool_calls": [
            {"id": c.id, "name": c.function.name, "arguments": c.function.arguments}
            for c in (message.tool_calls or [])]}


TOOL_FAILURE = {"error": "The tool failed unexpectedly. Tell the user the data isn't available right now."}


def _run_tool(session: Session, registry: ToolRegistry, call: Mapping[str, Any]) -> tuple[dict, ToolCallTrace]:
    arguments: Any = {}
    try:
        arguments = json.loads(call.get("arguments") or "{}")
        if not isinstance(arguments, dict):
            raise ToolError("Tool arguments must be a JSON object.")
        result, ok = sanitize_for_agent(registry.call(session, call["name"], arguments)), True
    except json.JSONDecodeError:
        result, ok = {"error": "Arguments were not valid JSON."}, False
    except TOOL_ERRORS as exc:   # bad arguments, unknown/ambiguous player, no data, source down
        result, ok = {"error": str(exc)}, False
    except Exception:   # a bug or DB error: keep the chat alive, keep details server-side
        log.exception("Tool %s failed", call.get("name"))
        session.rollback()
        result, ok = TOOL_FAILURE, False
    return result, ToolCallTrace(call["name"], arguments if isinstance(arguments, dict) else {}, ok)


def _assistant_message(content: str | None, calls: Sequence[Mapping[str, Any]]) -> dict:
    return {"role": "assistant", "content": content or "", "tool_calls": [
        {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
        for c in calls]}


def league_context(session: Session) -> str:
    """Tells the model which Yahoo leagues are imported, so 'my team' questions use the real roster."""
    leagues = my_leagues(session)
    if not leagues:
        return ""
    names = "; ".join(f"{league.name} (team: {team.name})" for league, team in leagues)
    return (f"The user's imported Yahoo leagues: {names}. For their team, waivers or trades, call get_my_team, "
            "get_league_waivers and find_league_trades (pass the league name when there's more than one) "
            "instead of asking them to list players. Use each league's own scoring.")


_names_cache: "WeakKeyDictionary[Any, frozenset[str]]" = WeakKeyDictionary()


def known_player_names(session: Session) -> frozenset[str]:
    """Full names of every skill-position player in the database (cached per engine)."""
    engine = session.get_bind()
    if engine not in _names_cache:
        rows = session.scalars(select(Player.full_name).where(Player.position.in_(POSITIONS)))
        _names_cache[engine] = frozenset(name for name in rows if name and " " in name)
    return _names_cache[engine]


def ungrounded_players(answer: str, messages: Sequence[Mapping[str, Any]], names: Iterable[str]) -> list[str]:
    """Players named in the answer who appear in no user message or tool result."""
    evidence = " ".join(str(m.get("content") or "") for m in messages if m.get("role") in ("user", "tool"))
    return sorted(name for name in names if name in answer and name not in evidence)


def run_agent(session: Session, client: LLMClient, history: Sequence[Mapping[str, str]],
              registry: ToolRegistry = TOOLS, max_rounds: int = MAX_ROUNDS) -> AgentReply:
    season = latest_season(session)
    week = latest_week(session, season) if season else None
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT.format(
                                season=season, week=week, leagues=league_context(session))},
                            *({"role": m["role"], "content": m["content"]} for m in history)]
    schemas, trace, revised = registry.openai_schemas(), [], False
    for _ in range(max_rounds):
        try:
            response = client.complete(messages, schemas)
        except ToolCallRejected as exc:
            # Let the model fix its own arguments instead of failing the whole chat.
            messages.append({"role": "system", "content": (
                f"Your last tool call was rejected before it ran: {exc}. Fix the arguments so they fit "
                "the tool's schema (for example, split a long player list across several calls) and try again.")})
            continue
        calls = response.get("tool_calls") or []
        if not calls:
            answer = response.get("content") or ""
            stray = ungrounded_players(answer, messages, known_player_names(session))
            if stray and not revised:
                # One revision: drop or check players no tool returned (the model's memory is stale).
                revised = True
                messages.append({"role": "assistant", "content": answer})
                messages.append({"role": "system", "content": (
                    f"Your answer names players that no tool returned in this conversation: {', '.join(stray)}. "
                    "Rewrite it using only players from tool results, or call a tool to check them first.")})
                continue
            return AgentReply(plain_text(answer), tuple(trace))
        messages.append(_assistant_message(response.get("content"), calls))
        for call in calls:
            result, record = _run_tool(session, registry, call)
            trace.append(record)
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": json.dumps(result, default=str)[:MAX_TOOL_RESULT_CHARS]})
    final = client.complete(messages, None)   # out of rounds: answer with what was gathered
    return AgentReply(plain_text(final.get("content") or ""), tuple(trace))


def plain_text(answer: str) -> str:
    """The chat shows plain text: drop markdown bold markers and heading hashes the model adds anyway."""
    lines = [line.lstrip().lstrip("#").lstrip() if line.lstrip().startswith("#") else line for line in answer.splitlines()]
    return "\n".join(lines).replace("**", "")
