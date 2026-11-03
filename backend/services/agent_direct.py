"""Ask AI answers that need no LLM: a few plain questions get the tool's result, rendered from a template.

Free AI tiers allow a handful of requests a minute (Groq) or a day (Gemini), and "show my roster"
doesn't need a model to read a list back. Matching is deliberately strict: the whole question,
lowercased and without punctuation, must be one of the phrases below (so no players, conditions or
second questions), and one league must be in scope (the chat's league or the only imported one).
Anything else, or a tool error, returns None and the normal LLM path answers.

Rendered text only uses tool results, which already wrap Yahoo team/league names with `label`.
"""
import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.orm import Session

from services import grounding
from services.agent import AgentReply, ToolCallTrace, clean_memory
from services.labels import label
from services.my_team import my_leagues
from tools.definitions import TOOLS, sanitize_for_agent
from tools.registry import ToolRegistry
from tools.shaping import dumps

log = logging.getLogger(__name__)

_POLITE_START = re.compile(r"^(?:(?:hey|ok|okay|so|please|can you|could you|pls)\s+)+")
_POLITE_END = re.compile(r"\s+(?:please|pls|thanks|thank you)$")
_PUNCTUATION = re.compile(r"[?.!,]+")

# intent -> the exact questions it answers (after normalize()).
PHRASES: dict[str, frozenset[str]] = {
    "roster": frozenset({
        "show my roster", "show me my roster", "my roster", "what's my roster", "what is my roster", "list my roster",
        "who's on my team", "who is on my team", "whos on my team", "who's on my roster", "who is on my roster",
        "show my team", "show me my team", "list my team", "my team"}),
    "lineup": frozenset({
        "who should i start", "who should i start this week", "set my lineup", "set my lineup for this week",
        "what's my best lineup", "what is my best lineup", "best lineup", "my best lineup", "show my best lineup",
        "optimize my lineup"}),
    "standings": frozenset({
        "standings", "my standings", "show standings", "show the standings", "show me the standings",
        "show my standings", "league standings", "what are the standings", "where do i stand"}),
    "power": frozenset({
        "power rankings", "my power rankings", "show power rankings", "show the power rankings",
        "show me the power rankings", "league power rankings", "what are the power rankings"}),
}
TOOL_FOR = {"roster": "get_my_team", "lineup": "get_my_team", "standings": "get_power_rankings",
            "power": "get_power_rankings"}
SLOT_ORDER = ("QB", "RB", "WR", "TE", "W/R/T", "W/R", "W/T", "Q/W/R/T", "K", "DEF", "BN", "IR")


def normalize(question: str) -> str:
    text = " ".join(question.lower().replace("’", "'").split())
    text = " ".join(_PUNCTUATION.sub(" ", text).split())
    return _POLITE_END.sub("", _POLITE_START.sub("", text))


def match_intent(question: str) -> str | None:
    """The one direct intent this exact question asks for, or None."""
    text = normalize(question)
    return next((intent for intent, phrases in PHRASES.items() if text in phrases), None)


def _league_name(session: Session, league_id: int | None) -> str | None:
    leagues = my_leagues(session)
    if league_id is not None:
        return next((league.name for league, _ in leagues if league.id == league_id), None)
    return leagues[0][0].name if len(leagues) == 1 else None


def _num(value: Any, digits: int = 1) -> str:
    return f"{value:.{digits}f}" if isinstance(value, (int, float)) and not isinstance(value, bool) else "?"


def _pct(value: Any) -> str | None:
    return f"{round(value * 100)}%" if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _slot_key(slot: str) -> int:
    return SLOT_ORDER.index(slot) if slot in SLOT_ORDER else len(SLOT_ORDER)


def _roster_text(result: Mapping[str, Any]) -> str:
    rows = sorted(result.get("roster") or [], key=lambda r: _slot_key(str(r.get("slot"))))
    lines = [f"Your roster in {result.get('league')} ({result.get('team')}), week {result.get('week')} projections:"]
    for row in rows:
        line = f"- {label(row.get('slot'))}: {label(row.get('player'))}, {_num(row.get('proj'))} proj"
        if row.get("opp"):
            line += f" {label(row['opp'])}"
        if row.get("status"):
            line += f" ({label(row['status'])})"
        if row.get("locked"):
            line += " [locked]"
        lines.append(line)
    if not rows:
        lines.append("- No players on this roster yet.")
    return "\n".join(lines)


def _lineup_text(result: Mapping[str, Any]) -> str:
    best = result.get("best_lineup") or {}
    lines = [f"Best lineup for week {result.get('week')} in {result.get('league')} (projections already count "
             "each player's chance to play):"]
    for slot in sorted(best, key=_slot_key):
        lines.append(f"- {label(slot)}: {', '.join(label(n) for n in best[slot]) or 'empty'}")
    changes = result.get("lineup_changes") or []
    lines.append("")
    lines += (["Changes to make:"] + [f"- {c}" for c in changes]) if changes else \
        ["Your current lineup already matches it."]
    alerts = result.get("alerts") or []
    if alerts:
        lines += ["", "Watch out:"] + [f"- {a}" for a in alerts]
    if result.get("locked"):
        lines += ["", f"Locked (game started): {', '.join(label(n) for n in result['locked'])}."]
    return "\n".join(lines)


def _wins(record: Any) -> tuple[float, ...]:
    """'5-2' or '5-2-1' -> (wins + half the ties, -losses); unknown records sort last."""
    parts = [int(p) for p in re.findall(r"\d+", str(record or ""))]
    if len(parts) < 2:
        return (-1.0, 0.0)
    ties = parts[2] if len(parts) > 2 else 0
    return (parts[0] + 0.5 * ties, -parts[1])


def _team_line(rank: int, team: Mapping[str, Any], standings: bool) -> str:
    you = " (you)" if team.get("is_users_team") else ""
    parts = [f"{team.get('record') or '?'}"]
    if standings:
        parts.append(f"{_num(team.get('points_for'))} points for")
    elif team.get("strength") is not None:
        parts.append(f"strength {_num(team.get('strength'))}")
    if (odds := _pct(team.get("playoff_odds"))) is not None:
        parts.append(f"{odds} playoff odds")
    return f"{rank}. {team.get('team')}{you}: {', '.join(parts)}"


def _teams_text(result: Mapping[str, Any], standings: bool) -> str:
    teams = list(result.get("teams") or [])
    if standings:
        teams.sort(key=lambda t: (*_wins(t.get("record")), t.get("points_for") or 0), reverse=True)
        head = f"Standings in {result.get('league')} (by record, then points for):"
    else:
        teams.sort(key=lambda t: t.get("power_rank") or len(teams) + 1)
        head = (f"Power rankings for {result.get('league')} (strength = best lineup's rest-of-season points "
                "per game):")
    lines = [head] + [_team_line(i, t, standings) for i, t in enumerate(teams, start=1)]
    if isinstance(result.get("playoff_teams"), int):
        lines.append(f"The top {result['playoff_teams']} make the playoffs; odds are this app's season simulation.")
    return "\n".join(lines)


RENDER = {"roster": _roster_text, "lineup": _lineup_text,
          "standings": lambda r: _teams_text(r, standings=True), "power": lambda r: _teams_text(r, standings=False)}


def answer_directly(session: Session, history: Sequence[Mapping[str, str]], memory: Sequence[str] = (),
                    league_id: int | None = None, registry: ToolRegistry = TOOLS) -> AgentReply | None:
    """An instant, template answer for the latest question, or None to use the LLM."""
    questions = [m.get("content") or "" for m in history if m.get("role") == "user"]
    intent = match_intent(questions[-1]) if questions else None
    league = _league_name(session, league_id) if intent else None
    if intent is None or league is None:
        return None
    name, arguments = TOOL_FOR[intent], {"league": league}
    try:
        result = sanitize_for_agent(registry.call(session, name, arguments))
        text = RENDER[intent](result)
    except Exception:   # tool error, source down or an unexpected shape: the LLM path handles (and explains) it
        log.info("Direct answer for %s fell back to the AI", intent, exc_info=True)
        session.rollback()
        return None
    kept = clean_memory(f for f in memory if grounding.safe_fact(f))
    return AgentReply(text, (ToolCallTrace(name, arguments, True),), kept, "answer", (), None, "app",
                      rounds=0, evidence=(dumps(result),))
