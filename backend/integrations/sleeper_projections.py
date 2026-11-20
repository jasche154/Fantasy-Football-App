"""Sleeper's weekly projections for past seasons (2018 on; RotoWire numbers), for Replay League.

An undocumented endpoint of Sleeper's public API. It returns a row for every player it knows (about
3,300), most of them placeholders without points; only rows projected above zero are kept. Sleeper scores
PPR, half-PPR and standard itself, kickers and defenses included. A row's nested `player` object is
today's metadata (current team), so only the name and position are read from it.
"""
from typing import Any

from integrations.adp_history import HistoryClient, HistoryError
from integrations.espn_history import WeekProjection
from integrations.weekly_sources import normalize_team

SOURCE = "sleeper"
URL = "https://api.sleeper.app/projections/nfl/{season}/{week}"
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
SAME_IN_EVERY_FORMAT = ("K", "DST")


def fetch_week(http: HistoryClient, season: int, week: int) -> list:
    params = [("season_type", "regular"), *(("position[]", p) for p in POSITIONS), ("order_by", "pts_ppr")]
    payload = http.get_json("Sleeper projections", URL.format(season=season, week=week), params=params)
    if not isinstance(payload, list):
        raise HistoryError("Sleeper projections returned an unexpected response.")
    return payload


def _points(stats: dict, key: str) -> float | None:
    value = stats.get(key)
    return round(float(value), 2) if isinstance(value, (int, float)) else None


def _projection(row: dict) -> WeekProjection | None:
    stats, player = row.get("stats") or {}, row.get("player") or {}
    ppr, source_id = _points(stats, "pts_ppr"), row.get("player_id")
    if not ppr or ppr <= 0 or not source_id:
        return None
    position = str(player.get("position") or "").upper()
    position = "DST" if position == "DEF" else position
    std = _points(stats, "pts_std")
    half = _points(stats, "pts_half_ppr")
    if position in SAME_IN_EVERY_FORMAT:
        std = half = ppr
    std = ppr if std is None else std
    half = round((ppr + std) / 2, 2) if half is None else half
    name = f"{player.get('first_name') or ''} {player.get('last_name') or ''}".strip()
    team = normalize_team(str(source_id)) if position == "DST" else None      # a defense's id is its team code
    return WeekProjection(str(source_id), name, position, ppr, half, std, team=team)


def parse_week(payload: Any) -> list[WeekProjection]:
    """Every player Sleeper projected above zero for the week."""
    rows = payload if isinstance(payload, list) else []
    projections = (_projection(row) for row in rows if isinstance(row, dict))
    return [p for p in projections if p is not None]


def week_projections(http: HistoryClient, season: int, week: int) -> list[WeekProjection]:
    return parse_week(fetch_week(http, season, week))
