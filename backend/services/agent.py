"""LLM assistant loop: the model chooses tools, the app runs them, the model explains.

The LLM client is injectable (`LLMClient` protocol), so tests use a scripted fake and the
app uses a free provider chain (services/llm_client.py). Answers are checked against what the
tools returned (services/grounding.py) before they reach the user.
"""
import json
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from sqlalchemy.orm import Session

from services import agent_routing, grounding
from services.agent_prompt import build_system_prompt, league_context, memory_context  # noqa: F401 (re-exported)
from services.llm_client import AgentUnavailable, GroqClient, ToolCallRejected  # noqa: F401 (re-exported)
from services.my_team import my_leagues
from services.player_index import get_index
from tools.definitions import TOOL_ERRORS, TOOLS, sanitize_for_agent
from tools.registry import ToolError, ToolRegistry
from tools.shaping import dumps, fit

log = logging.getLogger(__name__)

MAX_ROUNDS = 6
MAX_REJECTIONS = 2             # provider-rejected tool calls retried per turn (not counted as rounds)
MAX_MEMORY = 10                # facts the user asked the assistant to keep in mind
MAX_FACT_CHARS = 200
FACT_OVERLAP = 0.6             # share of a remembered fact's words that must come from the user's messages
TOOL_CHARS = 6_000             # one round of tool results, when the client has no provider profile
MIN_CALL_CHARS = 1_500         # floor per call when a round has many calls
INPUT_TOKENS = 6_000           # whole-request budget (chars / 4) when the client has no profile
CHARS_PER_TOKEN = 4
DIGEST_CHARS = 400             # earlier rounds' tool results shrink to this when a request is over budget
OUT_OF_ROUNDS = ("Out of tool rounds: answer now with the data above, and say plainly what you couldn't check.")
TOOL_FAILURE = {"error": "The tool failed unexpectedly. Tell the user the data isn't available right now."}


class LLMClient(Protocol):
    def complete(self, messages: list[dict], tools: list[dict] | None) -> dict:
        """Return {"content": str | None, "tool_calls": [{"id", "name", "arguments", "extra"?}], "usage"?}."""


@dataclass(frozen=True)
class ToolCallTrace:
    name: str
    arguments: Mapping[str, Any]
    ok: bool
    cached: bool = False


@dataclass(frozen=True)
class AgentReply:
    reply: str
    tool_calls: tuple[ToolCallTrace, ...]
    memory: tuple[str, ...] = ()
    stop_reason: Literal["answer", "out_of_rounds"] = "answer"
    unverified: tuple[str, ...] = ()
    provider: str | None = None
    # Diagnostics: not part of equality, and `evidence` (every tool result) never goes to the API.
    rounds: int = field(default=0, compare=False)
    evidence: tuple[str, ...] = field(default=(), compare=False, repr=False)


@dataclass
class _Turn:
    """Mutable state of one run_agent call (kept local to that call)."""
    session: Session
    registry: ToolRegistry
    history: Sequence[Mapping[str, str]]
    focus_league: str | None
    memory: list[str]
    trace: list[ToolCallTrace] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)       # full tool results, for grounding
    results: dict[str, tuple[Any, Sequence[str]]] = field(default_factory=dict)   # tool_call_id -> result
    cache: dict[tuple[str, str], tuple[str, Any, bool]] = field(default_factory=dict)


def _profile_value(client: LLMClient, name: str, default: int) -> int:
    value = getattr(getattr(client, "profile", None), name, None)
    return value if isinstance(value, int) and value > 0 else default


def _focus_args(registry: ToolRegistry, name: str, arguments: dict, focus_league: str | None) -> dict:
    """The chat's league goes to league tools when the model leaves `league` out."""
    if not focus_league or "league" in arguments:
        return arguments
    try:
        properties = registry.get(name).parameters.get("properties", {})
    except ToolError:
        return arguments
    return {**arguments, "league": focus_league} if "league" in properties else arguments


def _execute(turn: _Turn, name: str, arguments: dict) -> tuple[Any, bool]:
    try:
        return sanitize_for_agent(turn.registry.call(turn.session, name, arguments)), True
    except TOOL_ERRORS as exc:   # bad arguments, unknown/ambiguous player, no data, source down
        return {"error": str(exc)}, False
    except Exception:   # a bug or DB error: keep the chat alive, keep details server-side
        log.exception("Tool %s failed", name)
        turn.session.rollback()
        return TOOL_FAILURE, False


def _run_tool(turn: _Turn, call: Mapping[str, Any], budget: int) -> str:
    """Runs one call (or reuses an identical one from this turn) and returns the tool message text."""
    name = call["name"]
    try:
        arguments = json.loads(call.get("arguments") or "{}")
        if not isinstance(arguments, dict):
            raise ToolError("Tool arguments must be a JSON object.")
    except json.JSONDecodeError:
        turn.trace.append(ToolCallTrace(name, {}, False))
        return dumps({"error": "Arguments were not valid JSON."})
    except ToolError as exc:
        turn.trace.append(ToolCallTrace(name, {}, False))
        return dumps({"error": str(exc)})
    arguments = _focus_args(turn.registry, name, arguments, turn.focus_league)
    key = (name, json.dumps(arguments, sort_keys=True, default=str))
    if key in turn.cache:
        text, result, ok = turn.cache[key]
        turn.trace.append(ToolCallTrace(name, arguments, ok, cached=True))
        turn.results[call["id"]] = (result, _trim_keys(turn.registry, name))
        return text
    result, ok = _execute(turn, name, arguments)
    trim_keys = _trim_keys(turn.registry, name)
    text = fit(result, budget, trim_keys)
    turn.cache[key] = (text, result, ok)
    turn.results[call["id"]] = (result, trim_keys)
    turn.evidence.append(dumps(result))
    turn.trace.append(ToolCallTrace(name, arguments, ok))
    _maybe_remember(turn, name, arguments, ok)
    return text


def _trim_keys(registry: ToolRegistry, name: str) -> Sequence[str]:
    try:
        return tuple(getattr(registry.get(name), "trim_keys", ()))
    except ToolError:
        return ()


def _maybe_remember(turn: _Turn, name: str, arguments: Mapping[str, Any], ok: bool) -> None:
    fact = str(arguments.get("fact", "")) if name == "remember" else ""
    if ok and fact and user_said(fact, turn.history) and grounding.safe_fact(fact):
        turn.memory.append(fact)


def _assistant_message(content: str | None, calls: Sequence[Mapping[str, Any]]) -> dict:
    def tool_call(c: Mapping[str, Any]) -> dict:
        base = {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
        # Provider-specific fields (e.g. Gemini thought signatures) go back as they came.
        return {**(c.get("extra") or {}), **base}
    return {"role": "assistant", "content": content or "", "tool_calls": [tool_call(c) for c in calls]}


def _estimate_tokens(messages: Sequence[Mapping[str, Any]], schemas: Sequence[Mapping[str, Any]] | None) -> int:
    chars = sum(len(str(m.get("content") or "")) + len(json.dumps(m.get("tool_calls") or [])) for m in messages)
    return (chars + len(json.dumps(schemas or []))) // CHARS_PER_TOKEN


def _fit_request(messages: list[dict], schemas: list[dict] | None, turn: _Turn, budget_tokens: int) -> list[dict]:
    """Shrinks earlier rounds' tool results (oldest first) to digests while the request is over budget.

    The latest round stays whole; the full results stay in `turn.evidence` for grounding.
    """
    if _estimate_tokens(messages, schemas) <= budget_tokens:
        return messages
    last_assistant = max(i for i, m in enumerate(messages) if m["role"] == "assistant" or i == 0)
    shrunk = list(messages)
    for i, message in enumerate(messages[:last_assistant]):
        stored = turn.results.get(message.get("tool_call_id", "")) if message["role"] == "tool" else None
        if stored and len(message["content"]) > DIGEST_CHARS:
            shrunk[i] = {**message, "content": fit(stored[0], DIGEST_CHARS, stored[1])}
            if _estimate_tokens(shrunk, schemas) <= budget_tokens:
                break
    return shrunk


def _ground(answer: str, messages: Sequence[Mapping[str, Any]], turn: _Turn):
    """grounding skips this turn's drafts and notes itself; digested results come from `turn.evidence`."""
    evidence = grounding.collect_evidence(messages, turn.evidence)
    return grounding.check_answer(answer, evidence, get_index(turn.session))


def _route(session: Session, history: Sequence[Mapping[str, str]]) -> tuple[list[str], tuple[str, ...], tuple]:
    """(tools, intents, mentions) for the latest question, from the shared player index."""
    plan = agent_routing.route_plan(history, get_index(session))
    return list(plan.tools), tuple(plan.intents), tuple(plan.mentions)


def _finish(answer: str, report, turn: _Turn, client: LLMClient, stop: str, rounds: int) -> AgentReply:
    failed = tuple(dict.fromkeys(t.name for t in turn.trace if not t.ok))
    text = plain_text(answer)
    if not report.ok:
        note = grounding.caveat(report, failed)
        text = f"{text}\n\n{note}" if note else text
    return AgentReply(text, tuple(turn.trace), clean_memory(turn.memory), stop, tuple(report.unverified),
                      getattr(client, "last_provider", None), rounds, tuple(turn.evidence))


def _focus_league(session: Session, league_id: int | None) -> str | None:
    if league_id is None:
        return None
    name = next((league.name for league, _ in my_leagues(session) if league.id == league_id), None)
    if name is None:
        raise ValueError(f"League {league_id} isn't imported.")
    return name


def mentions_line(mentions: Iterable[Any]) -> str:
    """'Players mentioned: ...' from the routing index, so the model can skip a search round."""
    parts = []
    for mention in mentions:
        names = " / ".join(mention.names)
        if mention.ambiguous:
            parts.append(f"{json.dumps(mention.text[:40])} is ambiguous: {names} (ask which one, or check both)")
        elif names:
            parts.append(names)
    return f"Players the user mentioned (from the app's database): {'; '.join(parts)}." if parts else ""


def run_agent(session: Session, client: LLMClient, history: Sequence[Mapping[str, str]],
              registry: ToolRegistry = TOOLS, max_rounds: int = MAX_ROUNDS,
              memory: Sequence[str] = (), league_id: int | None = None) -> AgentReply:
    """Raises ValueError for an unknown `league_id`; AgentUnavailable when the model can't answer."""
    turn = _Turn(session, registry, history, _focus_league(session, league_id),
                 list(clean_memory(f for f in memory if grounding.safe_fact(f))))
    tools, intents, mentions = _route(session, history)
    system = build_system_prompt(session, memory=turn.memory, intents=intents,
                                 mentions=mentions_line(mentions), focus_league_id=league_id)
    messages: list[dict] = [{"role": "system", "content": system},
                            *({"role": m["role"], "content": m["content"]} for m in history)]
    schemas = registry.openai_schemas(tools)
    input_tokens = INPUT_TOKENS
    requests = rounds = rejections = 0
    revised = False
    while rounds < max_rounds:
        # A fallback chain may switch providers between requests, so read the budgets each round.
        input_tokens = _profile_value(client, "max_input_tokens", INPUT_TOKENS)
        tool_chars = _profile_value(client, "max_tool_chars", TOOL_CHARS)
        try:
            requests += 1
            response = client.complete(_fit_request(messages, schemas, turn, input_tokens), schemas)
        except ToolCallRejected as exc:
            rejections += 1
            if rejections > MAX_REJECTIONS:
                break
            # Let the model fix its own arguments instead of failing the whole chat.
            messages.append({"role": "system", "content": (
                f"Your last tool call was rejected before it ran: {exc}. Fix the arguments so they fit "
                "the tool's schema (for example, split a long player list across several calls) and try again.")})
            continue
        rounds += 1
        calls = response.get("tool_calls") or []
        if not calls:
            answer = response.get("content") or ""
            report = _ground(answer, messages, turn)
            if not report.ok and not revised:
                # One combined revision for stray names and numbers (the model's memory is stale).
                revised = True
                messages += [{"role": "assistant", "content": answer},
                             {"role": "user", "content": grounding.revision_message(report)}]
                continue
            return _finish(answer, report, turn, client, "answer", requests)
        messages.append(_assistant_message(response.get("content"), calls))
        budget = max(MIN_CALL_CHARS, tool_chars // len(calls))
        for call in calls:
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": _run_tool(turn, call, budget)})
    messages.append({"role": "system", "content": OUT_OF_ROUNDS})
    requests += 1
    final = client.complete(_fit_request(messages, None, turn, input_tokens), None)
    answer = final.get("content") or ""
    return _finish(answer, _ground(answer, messages, turn), turn, client, "out_of_rounds", requests)


def user_said(fact: str, history: Sequence[Mapping[str, str]]) -> bool:
    """A fact may be remembered only if it restates the user's own words, so text from a tool
    result (a news headline, a team name) can't plant instructions that persist in the prompt."""
    words = {w for w in re.findall(r"[a-z0-9'-]{3,}", fact.lower())}
    if not words:
        return False
    said = " ".join(m["content"].lower() for m in history if m.get("role") == "user")
    return sum(w in said for w in words) / len(words) >= FACT_OVERLAP


def clean_memory(facts: Iterable[str]) -> tuple[str, ...]:
    """Trimmed, de-duplicated, newest MAX_MEMORY facts."""
    unique = list(dict.fromkeys(f.strip()[:MAX_FACT_CHARS] for f in facts if f and f.strip()))
    return tuple(unique[-MAX_MEMORY:])


_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_BULLET = re.compile(r"^(\s*)[*•]\s+")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _flatten_tables(lines: list[str]) -> list[str]:
    """Markdown tables become '- a: b, c: d' lines (the chat shows plain text)."""
    out: list[str] = []
    header: list[str] | None = None
    for i, line in enumerate(lines):
        if line.count("|") < 2:
            header = None
            out.append(line)
        elif _TABLE_RULE.match(line):
            continue
        elif header is None and i + 1 < len(lines) and _TABLE_RULE.match(lines[i + 1]):
            header = _cells(line)
        else:
            cells = _cells(line)
            pairs = [f"{h}: {c}" if h else c for h, c in zip(header or [""] * len(cells), cells) if c]
            out.append("- " + ", ".join(pairs))
    return out


def plain_text(answer: str) -> str:
    """The chat shows plain text: drop markdown markers the model adds anyway, flatten tables, unify bullets."""
    lines = [line.lstrip().lstrip("#").lstrip() if line.lstrip().startswith("#") else line for line in answer.splitlines()]
    lines = [_BULLET.sub(r"\1- ", line) for line in _flatten_tables(lines)]
    return "\n".join(lines).replace("**", "").replace("__", "").replace("`", "")
