"""LLM assistant loop: the model chooses tools, the app runs them, the model explains.

The LLM client is injectable (`LLMClient` protocol), so tests use a scripted fake and the
app uses Groq. The model only ever sees numbers that came back from tools.
"""
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.orm import Session

from services.projections import latest_season, latest_week
from tools.definitions import TOOL_ERRORS, TOOLS, sanitize_for_agent
from tools.registry import ToolError, ToolRegistry

log = logging.getLogger(__name__)

MAX_ROUNDS = 6
MAX_TOOL_RESULT_CHARS = 12_000
SYSTEM_PROMPT = """You are a fantasy football analyst inside a FantasyPros-style app.

Rules:
- Get every number from a tool call. Never guess or invent stats, projections, or rankings.
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
- evaluate_trade: `give` = players the user sends, `get` = players the user receives. Verdicts
  "slight_win" / "big_win" mean the USER wins; "slight_loss" / "big_loss" mean the user loses.

Data available: NFL seasons through {season}, regular season through week {week}."""


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


def run_agent(session: Session, client: LLMClient, history: Sequence[Mapping[str, str]],
              registry: ToolRegistry = TOOLS, max_rounds: int = MAX_ROUNDS) -> AgentReply:
    season = latest_season(session)
    week = latest_week(session, season) if season else None
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT.format(season=season, week=week)},
                            *({"role": m["role"], "content": m["content"]} for m in history)]
    schemas, trace = registry.openai_schemas(), []
    for _ in range(max_rounds):
        response = client.complete(messages, schemas)
        calls = response.get("tool_calls") or []
        if not calls:
            return AgentReply(response.get("content") or "", tuple(trace))
        messages.append(_assistant_message(response.get("content"), calls))
        for call in calls:
            result, record = _run_tool(session, registry, call)
            trace.append(record)
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": json.dumps(result, default=str)[:MAX_TOOL_RESULT_CHARS]})
    final = client.complete(messages, None)   # out of rounds: answer with what was gathered
    return AgentReply(final.get("content") or "", tuple(trace))
