"""ESPN's pre-game weekly projections for past seasons (2018 on), for Replay League.

Same unofficial league-default endpoint as integrations/espn.py, with two differences: the stats filter
there returns no projected lines for 2018, and here the WHOLE player pool is requested. Sorting by points
and keeping the top N would let hindsight decide who is in the pool.

Skill players are scored from ESPN's projected stat line with this app's scoring (PPR, half, standard).
Kickers and defenses carry ESPN's own projected total in all three.
"""
import json
import logging
from dataclasses import dataclass
from typing import Any

from integrations import espn
from integrations.adp_history import HistoryClient, HistoryError, parse_rows
from services.scoring import scoring_for
from services.weekly_model import points_from_stats

log = logging.getLogger(__name__)

SOURCE = "espn"
POOL_LIMIT = 3000                       # ESPN lists about 1,100 players for a week
SLOT_IDS = [0, 2, 4, 6, 16, 17]         # QB, RB, WR, TE, D/ST, K lineup slots
KICKER, DEFENSE = 5, 16                 # defaultPositionId
DEFENSE_ID_BASE = -16000                # a D/ST's player id is -16000 - proTeamId
FORMATS = ("ppr", "half", "std")


@dataclass(frozen=True)
class WeekProjection:
    """One player's pre-game projection for a week, as a site published it."""

    source_id: str                # the site's player id
    name: str
    position: str                 # QB, RB, WR, TE, K, DST (other positions pass through as the site names them)
    pts_ppr: float
    pts_half: float
    pts_std: float
    team: str | None = None       # defenses only (nflverse code)


def fetch_week(http: HistoryClient, season: int, week: int) -> dict:
    players = {"filterStatsForSourceIds": {"value": [0, 1]}, "filterStatsForSplitTypeIds": {"value": [1]},
               "filterStatsForScoringPeriodIds": {"value": [week]}, "filterSlotIds": {"value": SLOT_IDS},
               "limit": POOL_LIMIT, "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}
    payload = http.get_json("ESPN projections", espn.URL.format(season=season),
                            params={"view": "kona_player_info", "scoringPeriodId": week},
                            headers={"X-Fantasy-Filter": json.dumps({"players": players})})
    if not isinstance(payload, dict) or not isinstance(payload.get("players"), list):
        raise HistoryError("ESPN projections returned an unexpected response.")
    if len(payload["players"]) >= POOL_LIMIT:
        log.warning("ESPN %s week %s: the player pool hit the request limit and may be cut short", season, week)
    return payload


def _applied_total(player: dict, season: int, week: int) -> float:
    """ESPN's own projected points for the week (0 when there is no projected line)."""
    return float((espn.projected_line(player, season, week) or {}).get("appliedTotal") or 0.0)


def _skill_points(player: dict, season: int, week: int, position: str) -> tuple[float, float, float]:
    stats = espn.projected_stats(player, season, week) or {}
    ppr, half, std = (points_from_stats(stats, position, scoring_for(fmt)) for fmt in FORMATS)
    return ppr, half, std


def _projection(player: dict, season: int, week: int) -> WeekProjection | None:
    position_id, name = player.get("defaultPositionId"), player.get("fullName")
    if not name or player.get("id") is None:
        return None
    if position_id in espn.POSITION_IDS:
        position, team = espn.POSITION_IDS[position_id], None
        points = _skill_points(player, season, week, position)
    elif position_id in (KICKER, DEFENSE):
        total = round(_applied_total(player, season, week), 2)
        position, points = ("K" if position_id == KICKER else "DST"), (total, total, total)
        team = espn.TEAMS.get(DEFENSE_ID_BASE - int(player["id"])) if position_id == DEFENSE else None
    else:
        return None
    if points[0] <= 0:              # byes, injured players and unranked depth: ESPN projects them at 0
        return None
    return WeekProjection(str(player["id"]), str(name), position, *points, team=team)


def parse_week(payload: Any, season: int, week: int) -> list[WeekProjection]:
    """Every player ESPN projected above zero for the season's week."""
    entries = (payload.get("players") or []) if isinstance(payload, dict) else []
    return parse_rows("ESPN projections", entries,
                      lambda entry: _projection(entry.get("player") or {}, season, week))


def week_projections(http: HistoryClient, season: int, week: int) -> list[WeekProjection]:
    return parse_week(fetch_week(http, season, week), season, week)
