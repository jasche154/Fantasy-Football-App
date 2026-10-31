"""Deterministic checks that an Ask AI answer only uses players and numbers the conversation supplied.

Evidence is what the model was shown: user messages, tool results, the system prompt (rosters
from our own database) and earlier assistant replies from the chat history. Not counted: the
draft being checked, the app's own correction messages, or lines already marked unverified, so
an unchecked claim can't vouch for itself. No LLM call: regex and set lookups only.
"""
import re
import unicodedata
from bisect import bisect_left
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

CAVEAT_PREFIX = "Not verified against the app's data:"
APP_CHECK_PREFIX = "[app check]"
MAX_FACT_CHARS = 200
DERIVED_POOL_MAX = 60       # beyond this many evidence numbers, sums/differences would match almost anything
DECIMAL_TOLERANCE = 0.051   # a value rounded to one decimal place
INTEGER_TOLERANCE = 0.5     # a value rounded to a whole number

_APOSTROPHES = str.maketrans({"‘": "'", "’": "'", "ʼ": "'", "`": "'", "´": "'"})
_JSON_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_EVIDENCE_NUMBER = re.compile(r"\d+(?:\.\d+)?")
# A number the answer states as a stat: a decimal, a percent, a dollar amount, or a count with a unit.
_ANSWER_NUMBER = re.compile(
    r"(?<![\w.])(?P<dollar>\$\s?)?(?P<num>\d{1,3}(?:,\d{3})+|\d+)(?P<frac>\.\d+)?"
    r"(?P<unit>\s?%|\s?percent\b|[\s-]?(?:fantasy\s)?(?:pts|points?|ppg)\b|\s?faab\b)?", re.IGNORECASE)
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
_META_FACT = re.compile(
    r"\b(?:ignore|disregard|always|never\s+say|instructions?|prompt|tools?|assistant|you\s+must|"
    r"you\s+should|from\s+now\s+on|pretend|act\s+as|respond|reply\s+with|https?)\b|<\|", re.IGNORECASE)


class Mention(Protocol):
    text: str
    names: tuple[str, ...]
    ambiguous: bool


class NameIndex(Protocol):
    """What grounding needs from services.player_index.PlayerIndex."""
    full_names: Iterable[str]

    def mentions(self, text: str) -> Sequence[Mention]: ...


@dataclass(frozen=True)
class Evidence:
    text: str
    numbers: tuple[float, ...]   # sorted, distinct


@dataclass(frozen=True)
class GroundingReport:
    players: tuple[str, ...] = ()
    numbers: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.players and not self.numbers

    @property
    def unverified(self) -> tuple[str, ...]:
        return self.players + self.numbers


def fold(text: str) -> str:
    """NFKC, JSON \\u escapes decoded, curly apostrophes made straight."""
    decoded = _JSON_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text)
    return unicodedata.normalize("NFKC", decoded).translate(_APOSTROPHES)


def name_key(text: str) -> str:
    """Lowercase words without accents, apostrophes, periods or suffixes: 'A.J. Brown' == 'AJ Brown'."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", fold(text)) if not unicodedata.combining(c))
    words = re.sub(r"[^a-z0-9]+", " ", re.sub(r"['.]", "", plain.lower())).split()
    return " ".join(w for w in words if w not in _SUFFIXES)


def _content(message: Mapping[str, Any]) -> str:
    return str(message.get("content") or "")


def _is_app_check(message: Mapping[str, Any]) -> bool:
    return message.get("role") == "user" and _content(message).startswith(APP_CHECK_PREFIX)


def _evidence_texts(messages: Sequence[Mapping[str, Any]]) -> list[str]:
    real_users = [i for i, m in enumerate(messages) if m.get("role") == "user" and not _is_app_check(m)]
    last_user = real_users[-1] if real_users else len(messages)
    texts = []
    for i, message in enumerate(messages):
        role = message.get("role")
        if role == "tool" or (role == "user" and not _is_app_check(message)):
            texts.append(_content(message))
        elif role in ("system", "assistant") and i < last_user:   # the prompt and earlier turns, not this turn's drafts
            lines = _content(message).splitlines()
            texts.append("\n".join(line for line in lines if not line.lstrip().startswith(CAVEAT_PREFIX)))
    return texts


def collect_evidence(messages: Sequence[Mapping[str, Any]], extra_texts: Iterable[str] = ()) -> Evidence:
    """Everything the model was shown; `extra_texts` holds full tool results that were digested to save tokens."""
    text = fold("\n".join([*_evidence_texts(messages), *extra_texts]))
    numbers = sorted({float(n) for n in _EVIDENCE_NUMBER.findall(text)})
    return Evidence(text, tuple(numbers))


def _spelled_out(mention: Mention) -> bool:
    """The mention is a player's own name words ("Nacua", "Puka", "St. Brown"), not a typo match or nickname."""
    words = set(name_key(mention.text).split())
    return bool(words) and any(words <= set(name_key(n).split()) for n in mention.names)


def _unknown_players(answer: str, evidence: Evidence, index: NameIndex) -> list[str]:
    said = f" {name_key(evidence.text)} "

    def present(key: str) -> bool:
        return bool(key) and f" {key} " in said

    answer_key = f" {name_key(answer)} "
    stray = [name for name in sorted(index.full_names)
             if (key := name_key(name)) and f" {key} " in answer_key and not present(key)]
    for mention in index.mentions(fold(answer)):
        if not mention.names or not _spelled_out(mention):
            continue   # typo guesses and nicknames are routing aids, too loose to reject an answer on
        if present(name_key(mention.text)) or any(present(name_key(n)) for n in mention.names):
            continue   # an ambiguous last name is fine if any of its players was in the evidence
        label = mention.names[0] if len(mention.names) == 1 else mention.text
        if label not in stray and not any(name_key(label) in name_key(s) for s in stray):
            stray.append(label)
    return stray


def _within(value: float, pool: Sequence[float], tolerance: float) -> bool:
    i = bisect_left(pool, value - tolerance)
    return i < len(pool) and pool[i] <= value + tolerance


def _derived(value: float, pool: Sequence[float], tolerance: float) -> bool:
    """value = a - b or a + b for two numbers in the pool (a gap or a total the model worked out). Held to
    one-decimal precision even for whole numbers, or small integers would match almost any pair."""
    tolerance = min(tolerance, DECIMAL_TOLERANCE)
    return any(_within(a + value, pool, tolerance) or _within(value - a, pool, tolerance) for a in pool)


def _number_ok(value: float, percent: bool, tolerance: float, evidence: tuple[float, ...],
               derived_pool: Sequence[float]) -> bool:
    targets = [value, value / 100] if percent else [value]
    if percent:
        tolerance_for = lambda t: tolerance if t == value else tolerance / 100   # noqa: E731
    else:
        tolerance_for = lambda t: tolerance   # noqa: E731
    if any(_within(t, evidence, tolerance_for(t)) for t in targets):
        return True
    return any(_derived(t, derived_pool, tolerance_for(t)) for t in targets)


def _answer_numbers(answer: str) -> list[tuple[str, float, bool, float]]:
    """(as written, value, is_percent, tolerance) for numbers stated as stats; bare integers are exempt."""
    found = []
    for match in _ANSWER_NUMBER.finditer(answer):
        unit = (match.group("unit") or "").strip().lower()
        if not (match.group("frac") or match.group("dollar") or unit):
            continue   # weeks, seasons, ranks, counts, records
        value = float(match.group("num").replace(",", "") + (match.group("frac") or ""))
        tolerance = DECIMAL_TOLERANCE if match.group("frac") else INTEGER_TOLERANCE
        found.append((match.group(0).strip(), value, unit in ("%", "percent"), tolerance))
    return found


def _unknown_numbers(answer: str, evidence: Evidence) -> list[str]:
    stated = _answer_numbers(fold(answer))
    grounded = [value for _, value, percent, tol in stated
                if _number_ok(value, percent, tol, evidence.numbers, ())]
    pool = sorted(set(grounded) | (set(evidence.numbers) if len(evidence.numbers) <= DERIVED_POOL_MAX else set()))
    stray = [text for text, value, percent, tol in stated
             if not _number_ok(value, percent, tol, evidence.numbers, pool)]
    return list(dict.fromkeys(stray))


def check_answer(answer: str, evidence: Evidence, index: NameIndex) -> GroundingReport:
    """Players and numbers in the answer that nothing in the evidence supports."""
    return GroundingReport(tuple(_unknown_players(answer, evidence, index)), tuple(_unknown_numbers(answer, evidence)))


def revision_message(report: GroundingReport) -> str:
    """One user-role correction covering every problem, so a single extra call can fix them all."""
    parts = [APP_CHECK_PREFIX]
    if report.players:
        parts.append("Your answer names players that no tool returned in this conversation: "
                     f"{', '.join(report.players)}.")
    if report.numbers:
        parts.append(f"It states numbers that no tool result contains: {', '.join(report.numbers)}.")
    parts.append("Rewrite it using only players and numbers from the tool results (quote numbers as given), "
                 "or call a tool to check them first.")
    return " ".join(parts)


def caveat(report: GroundingReport, failed_tools: Sequence[str] = ()) -> str:
    """Lines appended to an answer that still isn't fully grounded; empty when there's nothing to say."""
    lines = []
    if not report.ok:
        lines.append(f"{CAVEAT_PREFIX} {', '.join(report.unverified)}. Double-check before acting.")
    failed = list(dict.fromkeys(failed_tools))
    if failed:
        lines.append(f"Some data was unavailable ({', '.join(failed)}).")
    return "\n".join(lines)


def safe_fact(fact: str) -> bool:
    """A remembered fact from the browser is a short, plain statement, never a rule for the assistant."""
    text = (fact or "").strip()
    return (0 < len(text) <= MAX_FACT_CHARS and all(c.isprintable() for c in text)
            and not _META_FACT.search(fold(text)))
