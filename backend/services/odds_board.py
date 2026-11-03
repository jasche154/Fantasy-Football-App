"""The Odds page: a week's lines and the player props already stored. Read-only.

This never calls The Odds API (credits are scarce). Lines are nflverse's closing spread and
total from nfl_games; props are whatever weekly_refresh already bought into
weekly_source_projections (source "vegas_props"), once per game within 2 hours of kickoff.
"""
import json
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import NflGame, WeeklySourceProjection
from integrations.weekly_sources import utc
from services.schedule_strength import Game, load_schedule

PROPS_SOURCE = "vegas_props"
PROP_STATS = ("pass_yds", "pass_tds", "ints", "rush_yds", "rec", "rec_yds", "scrimmage_tds")
LAST_WEEK = 18
NO_PROPS_NOTE = ("No player props are stored for this week yet. Props are bought once per game within "
                 "2 hours of kickoff to save API credits, so they appear here game by game on game day.")


def latest_schedule_season(session: Session) -> int | None:
    return session.scalar(select(func.max(NflGame.season)))


def default_week(session: Session, season: int, today: date | None = None) -> int:
    """The first week with a game left, else the last week of the regular season."""
    return min(max(load_schedule(session, season).current_week(today) or LAST_WEEK, 1), LAST_WEEK)


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def _prop_row(row: WeeklySourceProjection) -> dict:
    try:
        parsed = json.loads(row.stats) if row.stats else {}
    except (TypeError, ValueError):
        parsed = {}
    stats = {k: parsed[k] for k in PROP_STATS if isinstance(parsed.get(k), (int, float))}
    return {"player_id": row.player_id, "name": row.player_name, "position": row.position,
            "team": row.team, "opponent": row.opponent, "stats": stats, "fetched_at": _iso(utc(row.fetched_at))}


def _game_row(game: Game, props: list[dict]) -> dict:
    kickoff = game.kickoff
    fetched = max((p["fetched_at"] for p in props), default=None)
    lead = (round((kickoff - datetime.fromisoformat(fetched)).total_seconds() / 3600, 1)
            if kickoff and fetched else None)
    return {
        "home_team": game.home_team, "away_team": game.away_team, "gameday": game.gameday,
        "gametime": game.gametime, "kickoff": _iso(kickoff), "final": game.is_final,
        "home_score": game.home_score, "away_score": game.away_score,
        "spread_line": game.spread_line, "total_line": game.total_line,
        "home_implied": game.implied_points(game.home_team), "away_implied": game.implied_points(game.away_team),
        "props": sorted(props, key=lambda p: (p["team"] != game.away_team, p["position"] or "", p["name"])),
        "props_fetched_at": fetched, "props_hours_before_kickoff": lead,
    }


def odds_board(session: Session, season: int, week: int) -> dict:
    games = [g for g in load_schedule(session, season).games if g.week == week]
    rows = session.scalars(select(WeeklySourceProjection).where(
        WeeklySourceProjection.season == season, WeeklySourceProjection.week == week,
        WeeklySourceProjection.source == PROPS_SOURCE)).all()
    by_team: dict[str, list[dict]] = {}
    for row in rows:
        if row.team:
            by_team.setdefault(row.team, []).append(_prop_row(row))
    board = [_game_row(g, by_team.get(g.home_team, []) + by_team.get(g.away_team, [])) for g in games]
    board.sort(key=lambda g: (g["kickoff"] or "9999", g["away_team"]))
    props_count = sum(len(g["props"]) for g in board)
    return {"season": season, "week": week, "games": board, "props_count": props_count,
            "note": None if props_count else NO_PROPS_NOTE}
