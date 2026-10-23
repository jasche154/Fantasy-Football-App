"""ESPN Fantasy weekly projections (the numbers on ESPN's player pages).

Uses ESPN's public, unofficial fantasy read API (no login for league-default data). It may
change without notice; this client is for personal use and caches results. Projected raw
stats are stored rather than ESPN's points, so they can be scored with any league settings.
"""
import json

import httpx
from sqlalchemy.orm import Session

from integrations.weekly_sources import (
    USER_AGENT, PlayerMatcher, SourceError, replace_snapshot, source_row,
)

URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leaguedefaults/3"
SOURCE = "espn"
PLAYER_LIMIT = 600
PROJECTED, WEEKLY_SPLIT = 1, 1
POSITION_IDS = {1: "QB", 2: "RB", 3: "WR", 4: "TE"}
SLOT_IDS = [0, 2, 4, 6]   # QB, RB, WR, TE lineup slots
# ESPN stat ids -> this app's projected-stat keys (see services.weekly_model.points_from_stats).
STAT_IDS = {"3": "pass_yds", "4": "pass_tds", "20": "ints", "24": "rush_yds", "25": "rush_tds",
            "53": "rec", "42": "rec_yds", "43": "rec_tds", "72": "fumbles_lost"}
TWO_POINT_IDS = ("19", "26", "44")
TEAMS = {1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN", 8: "DET", 9: "GB",
         10: "TEN", 11: "IND", 12: "KC", 13: "LV", 14: "LA", 15: "MIA", 16: "MIN", 17: "NE",
         18: "NO", 19: "NYG", 20: "NYJ", 21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF",
         26: "SEA", 27: "TB", 28: "WAS", 29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU"}


def fetch_week(http: httpx.Client, season: int, week: int) -> dict:
    player_filter = {"players": {
        "filterStatsForTopScoringPeriodIds": {"value": 1, "additionalValue": [f"11{season}{week}"]},
        "filterSlotIds": {"value": SLOT_IDS}, "limit": PLAYER_LIMIT,
        "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}}
    try:
        response = http.get(URL.format(season=season),
                            params={"scoringPeriodId": week, "view": "kona_player_info"},
                            headers={"User-Agent": USER_AGENT, "X-Fantasy-Filter": json.dumps(player_filter)})
    except httpx.HTTPError as exc:
        raise SourceError(f"ESPN projections request failed: {exc}") from exc
    if response.status_code >= 400:
        raise SourceError(f"ESPN projections returned HTTP {response.status_code}.")
    try:
        return response.json()
    except ValueError as exc:
        raise SourceError("ESPN projections returned a non-JSON response.") from exc


def projected_stats(player: dict, week: int) -> dict[str, float] | None:
    """The player's projected stat line for the week, or None (bye week / no projection)."""
    for line in player.get("stats") or []:
        if (line.get("statSourceId") == PROJECTED and line.get("scoringPeriodId") == week
                and line.get("statSplitTypeId") == WEEKLY_SPLIT and line.get("stats")):
            raw = line["stats"]
            stats = {key: float(raw[stat_id]) for stat_id, key in STAT_IDS.items() if raw.get(stat_id)}
            stats["two_pt"] = sum(float(raw.get(stat_id) or 0) for stat_id in TWO_POINT_IDS)
            return stats
    return None


def projection_rows(payload: dict, season: int, week: int, matcher: PlayerMatcher) -> list[dict]:
    rows = []
    for entry in payload.get("players") or []:
        player = entry.get("player") or {}
        position = POSITION_IDS.get(player.get("defaultPositionId"))
        stats = projected_stats(player, week) if position else None
        if not stats or not player.get("fullName"):
            continue
        team = TEAMS.get(player.get("proTeamId"))
        player_id = matcher.match(player["fullName"], team, position, espn_id=str(player.get("id")))
        row = source_row(season, week, SOURCE, player_id=player_id, name=player["fullName"],
                         position=position, team=team, stats=stats)
        owned = (player.get("ownership") or {}).get("percentOwned")
        row["pct_rostered"] = round(float(owned), 1) if isinstance(owned, (int, float)) else None
        rows.append(row)
    return rows


def refresh(session: Session, http: httpx.Client, season: int, week: int) -> int:
    payload = fetch_week(http, season, week)
    if not isinstance(payload, dict):
        raise SourceError("ESPN projections returned an unexpected response.")
    rows = projection_rows(payload, season, week, PlayerMatcher(session))
    return replace_snapshot(session, season, week, SOURCE, rows)

