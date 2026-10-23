"""Yahoo Sports weekly expert rankings (the tables in Yahoo's rankings articles).

Yahoo's rankings pages load their tables from a public JSON endpoint
(sports.yahoo.com/api/fanPro). It's unofficial and undocumented, so it may change without
notice; this client is for personal use, caches results, and only stores the latest
snapshot for each week. Weekly ranks are positional (QB1, RB1, ...).

Stored per player: the 40+ expert consensus rank plus the individual ranks of the experts
in TRACKED_EXPERTS (Josh Norris and Hayden Winks lead the weekly blend).
"""
import httpx
from sqlalchemy.orm import Session

from integrations.weekly_sources import (
    USER_AGENT, PlayerMatcher, SourceError, replace_snapshot, source_row,
)
from services.league import POSITIONS

URL = "https://sports.yahoo.com/api/fanPro/"
SOURCE = "yahoo_experts"
CONSENSUS = "consensus"
SCORING_PARAMS = {"ppr": "PPR", "half": "HALF", "std": "STD"}
JOSH_AND_HAYDEN = {"1408": "Josh Norris", "7666": "Hayden Winks"}
TRACKED_EXPERTS = {**JOSH_AND_HAYDEN, "285": "Dalton Del Don", "317": "Justin Boone",
                   "747": "Matt Harmon", "7": "Andy Behrens", "9": "Scott Pianowski"}


def fetch_position(http: httpx.Client, season: int, week: int, position: str, scoring: str) -> dict:
    params = {"sport": "NFL", "experts": "show", "year": season, "scoring": SCORING_PARAMS[scoring],
              "position": position, "type": "WK", "week": week, "wtype": "WK"}
    try:
        response = http.get(URL, params=params, headers={"User-Agent": USER_AGENT})
    except httpx.HTTPError as exc:
        raise SourceError(f"Yahoo rankings request failed: {exc}") from exc
    if response.status_code >= 400:
        raise SourceError(f"Yahoo rankings returned HTTP {response.status_code} for {position}.")
    try:
        return response.json()
    except ValueError as exc:
        raise SourceError("Yahoo rankings returned a non-JSON response.") from exc


def _rank(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def ranking_rows(payload: dict, season: int, week: int, scoring: str, matcher: PlayerMatcher) -> list[dict]:
    """One consensus row per player plus one row per tracked expert who ranked them."""
    rows = []
    for item in payload.get("players") or []:
        position, name = item.get("position"), item.get("name")
        if position not in POSITIONS or not name:
            continue
        yahoo_id = item.get("playerYahooId")
        player_id = matcher.match(name, item.get("team"), position,
                                  yahoo_id=str(yahoo_id) if yahoo_id is not None else None)
        opponent = next(iter((item.get("opponent") or "").split()[-1:]), None)   # "vs. GB" -> "GB"
        common = dict(player_id=player_id, name=name, position=position, team=item.get("team"),
                      opponent=opponent, scoring=scoring)
        rows.append(source_row(season, week, SOURCE, expert=CONSENSUS, rank=_rank(item.get("rank")), **common))
        for expert_id, rank in (item.get("experts") or {}).items():
            if expert_id in TRACKED_EXPERTS and _rank(rank) is not None:
                rows.append(source_row(season, week, SOURCE, expert=expert_id, rank=_rank(rank), **common))
    return rows


def refresh(session: Session, http: httpx.Client, season: int, week: int) -> int:
    """Fetch every position in every scoring format, then replace the week's snapshot.

    Everything is fetched before anything is stored, so a failure partway through never
    leaves one scoring format fresh and another stale. The first failed request stops it.
    """
    matcher = PlayerMatcher(session)
    by_scoring: dict[str, list[dict]] = {}
    for scoring in SCORING_PARAMS:
        rows = []
        for position in POSITIONS:
            payload = fetch_position(http, season, week, position, scoring)
            if not isinstance(payload, dict):
                raise SourceError("Yahoo rankings returned an unexpected response.")
            rows.extend(ranking_rows(payload, season, week, scoring, matcher))
        by_scoring[scoring] = rows
    return sum(replace_snapshot(session, season, week, SOURCE, rows, scoring=scoring)
               for scoring, rows in by_scoring.items())
