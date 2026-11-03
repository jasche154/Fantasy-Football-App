"""Manager tags for the user's Yahoo league, each with the evidence behind it.

From Yahoo's standings (trades, moves, FAAB balance), the season's transactions (winning bids,
the positions each manager picks up and trades for) and the retro trade grades
(services/trade_grades.py):
(Trade volume is the tendency label in league_insights: "Active trader".)
- "FAAB whale" (FAAB leagues): average winning bid at least WHALE_BID_SHARE of the budget.
- "Sits on FAAB" (FAAB leagues, from week SITS_AFTER_WEEK on, FAAB balance known): spent under SITS_SPENT_SHARE of it.
- "Waiver hawk": HAWK_ADDS_PER_WEEK+ moves per week played.
- "<POS> hunter": HUNTER_SHARE+ of a manager's acquisitions (adds and trades for, at least
  HUNTER_MIN_ACQUISITIONS) at one position.
- "Trade shark" / "Overpays": won / lost SHARK_TRADES+ graded trades by the win margin.
"""
import re
from collections import Counter
from collections.abc import Mapping, Sequence

from services.trade_grades import WIN_MARGIN

POSITION_CODE = re.compile(r"[A-Z]{1,4}")          # QB, RB, WR, TE, K, DEF...

WHALE_BID_SHARE = 0.12
SITS_SPENT_SHARE = 0.25
SITS_AFTER_WEEK = 6
HAWK_ADDS_PER_WEEK = 1.5
HUNTER_SHARE = 0.5
HUNTER_MIN_ACQUISITIONS = 4
SHARK_TRADES = 2


def _position(raw: str | None) -> str | None:
    """'RB,WR' -> 'RB' (Yahoo's dual eligibility); '' or anything that isn't a position code -> None,
    so text from Yahoo can't ride into a tag."""
    first = (raw or "").split(",")[0].strip()
    return first if POSITION_CODE.fullmatch(first) else None


def _tag(name: str, evidence: str) -> dict:
    return {"tag": name, "evidence": evidence}


def _activity(transactions: Sequence[Mapping]) -> tuple[dict[str, list[float]], dict[str, Counter]]:
    """({team: winning FAAB bids}, {team: acquisitions by position})."""
    bids: dict[str, list[float]] = {}
    acquired: dict[str, Counter] = {}
    for transaction in transactions:
        for move in transaction.get("moves", []):
            team = move.get("to_team")
            if not team or move.get("type") not in ("add", "trade"):
                continue
            if move["type"] == "add" and move.get("faab_bid") is not None:
                bids.setdefault(team, []).append(move["faab_bid"])
            position = _position(move.get("position"))
            if position:
                acquired.setdefault(team, Counter())[position] += 1
    return bids, acquired


def _faab_tags(standing: Mapping, bids: Sequence[float], budget: float, weeks_played: int) -> list[dict]:
    tags = []
    if bids and sum(bids) / len(bids) >= WHALE_BID_SHARE * budget:
        share = sum(bids) / len(bids) / budget
        tags.append(_tag("FAAB whale", f"avg winning bid {share:.0%} of ${budget:g}"))
    balance = standing.get("faab_balance")              # unknown: no tag (the bid list may be cut off)
    spent = budget - balance if balance is not None else None
    if spent is not None and weeks_played >= SITS_AFTER_WEEK and spent < SITS_SPENT_SHARE * budget:
        tags.append(_tag("Sits on FAAB", f"spent ${max(spent, 0):g} of ${budget:g} through week {weeks_played}"))
    return tags


def team_tags(standing: Mapping, bids: Sequence[float], acquired: Counter, *, uses_faab: bool,
              budget: float | None, weeks_played: int, record: Mapping[str, int] | None = None) -> list[dict]:
    """The tags one manager has earned, each with its evidence."""
    tags = []
    if uses_faab and budget:
        tags += _faab_tags(standing, bids, float(budget), weeks_played)
    moves = standing.get("moves") or 0
    if weeks_played > 0 and moves / weeks_played >= HAWK_ADDS_PER_WEEK:
        tags.append(_tag("Waiver hawk", f"{moves} moves in {weeks_played} weeks"))
    total = sum(acquired.values())
    if total >= HUNTER_MIN_ACQUISITIONS:
        position, count = acquired.most_common(1)[0]
        if count / total >= HUNTER_SHARE:
            tags.append(_tag(f"{position} hunter", f"{count / total:.0%} of {total} adds and trades were {position}"))
    record = record or {}
    margin = f"{WIN_MARGIN:g}+ pts"
    if record.get("won", 0) >= SHARK_TRADES:
        tags.append(_tag("Trade shark", f"won {record['won']} of {record['graded']} graded trades by {margin}"))
    if record.get("lost", 0) >= SHARK_TRADES:
        tags.append(_tag("Overpays", f"lost {record['lost']} of {record['graded']} graded trades by {margin}"))
    return tags


def manager_tags(standings: Sequence[Mapping], transactions: Sequence[Mapping], *, uses_faab: bool = False,
                 budget: float | None = None, weeks_played: int = 0,
                 records: Mapping[str, Mapping[str, int]] | None = None) -> dict[str, list[dict]]:
    """{team key: [{tag, evidence}]} for every team in the standings."""
    bids, acquired = _activity(transactions)
    records = records or {}
    return {s["team_key"]: team_tags(s, bids.get(s["team_key"], []), acquired.get(s["team_key"], Counter()),
                                     uses_faab=uses_faab, budget=budget, weeks_played=weeks_played,
                                     record=records.get(s["team_key"]))
            for s in standings}
