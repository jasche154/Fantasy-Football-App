"""Pure checks for one Ask AI answer (no model, no network). Used by services/agent_eval.py.

Each check returns True (pass), False (fail) or None (doesn't apply to this case). Grounding reuses
services/grounding.py, so the evaluation and the live answer check agree on what "grounded" means.
"""
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from services import grounding
from services.eval_cases import DECISION, ROSTER_ASKS, Case

# Phrases no answer should use: the app makes estimates, not promises, and FantasyPros was dropped.
GLOBAL_FORBID = (r"\bguarantee(d)?\b", r"\bcan'?t[- ]miss\b", r"\bsure thing\b", r"fantasypros")
MARKDOWN = re.compile(r"^\s*(#{1,6}\s|\|.*\|\s*$)|\*\*", re.MULTILINE)
SHORT_ANSWER_CHARS = 600         # an out-of-scope reply should be a brief decline, not an essay


@dataclass(frozen=True)
class CaseResult:
    checks: Mapping[str, bool | None]
    problems: tuple[str, ...]
    unverified: tuple[str, ...] = field(default=())

    @property
    def passed(self) -> bool:
        return not self.problems


def answer_body(reply: str) -> str:
    """The answer without the app's own caveat lines (those list the unverified items on purpose)."""
    return "\n".join(line for line in reply.splitlines() if not line.lstrip().startswith(grounding.CAVEAT_PREFIX))


def check_tools(case: Case, called: Sequence[str]) -> tuple[bool | None, list[str]]:
    used = set(called) - {"remember"} if case.no_tools else set(called)
    problems = []
    if case.no_tools and used:
        problems.append(f"expected no tools, called {sorted(used)}")
    if case.expect_any and not set(case.expect_any) & used:
        problems.append(f"expected one of {list(case.expect_any)}, called {list(called) or 'nothing'}")
    missing = [t for t in case.require_all if t not in used]
    if missing:
        problems.append(f"missing required tools {missing}")
    banned = sorted(set(case.forbid_tools) & used)
    if banned:
        problems.append(f"called forbidden tools {banned}")
    applies = bool(case.no_tools or case.expect_any or case.require_all or case.forbid_tools)
    return (not problems) if applies else None, problems


def check_decision(case: Case, reply: str) -> bool | None:
    return bool(re.search(DECISION, reply.lower())) if case.decision else None


def check_mentions(case: Case, reply: str) -> tuple[bool | None, list[str]]:
    lowered = reply.lower()
    missing = [p for p in case.must_mention if not re.search(p, lowered)]
    found = [p for p in (*case.forbid, *GLOBAL_FORBID) if re.search(p, lowered)]
    problems = [f"doesn't mention /{p}/" for p in missing] + [f"says forbidden /{p}/" for p in found]
    return not problems, problems


def check_roster_ask(case: Case, reply: str) -> bool | None:
    if not case.must_not_ask_roster:
        return None
    return not any(p in reply.lower() for p in ROSTER_ASKS)


def check_grounding(reply: str, messages: Sequence[Mapping[str, Any]], evidence: Sequence[str],
                    index: grounding.NameIndex) -> grounding.GroundingReport:
    """Players and numbers in the answer that no user turn, prompt, earlier reply or tool result supports."""
    return grounding.check_answer(answer_body(reply), grounding.collect_evidence(messages, evidence), index)


def score_answer(case: Case, reply: str, tools: Sequence[str], ok: Sequence[bool],
                 report: grounding.GroundingReport | None = None) -> CaseResult:
    """Every check for one answer. `report` is check_grounding's result (None: grounding not checked)."""
    tools_ok, problems = check_tools(case, tools)
    mentions_ok, mention_problems = check_mentions(case, reply)
    problems += mention_problems
    checks: dict[str, bool | None] = {
        "tools": tools_ok,
        "non_empty": bool(reply.strip()),
        "decision": check_decision(case, reply),
        "mentions": mentions_ok,
        "no_roster_ask": check_roster_ask(case, reply),
        "tool_errors": (any(ok) if tools else None),
        "plain_text": not MARKDOWN.search(reply),
        "short": (len(reply) <= SHORT_ANSWER_CHARS) if case.no_tools else None,
        "players_grounded": (not report.players) if report else None,
        "numbers_grounded": (not report.numbers) if report else None,
    }
    labels = {"non_empty": "empty reply", "decision": "no clear decision", "no_roster_ask": "asked for the roster",
              "tool_errors": "every tool call failed", "plain_text": "markdown in the reply",
              "short": "out-of-scope reply too long"}
    problems += [text for key, text in labels.items() if checks[key] is False]
    if report and report.players:
        problems.append(f"ungrounded players {list(report.players)}")
    if report and report.numbers:
        problems.append(f"ungrounded numbers {list(report.numbers)}")
    return CaseResult(checks, tuple(problems), report.unverified if report else ())


def score_live(case: Case, reply: str, tools: list[str], ok: list[bool]) -> list[str]:
    """Problems with one answer, without grounding (kept for older callers)."""
    return list(score_answer(case, reply, tools, ok).problems)
