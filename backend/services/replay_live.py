"""Replay League: which NFL season is in progress, and how far its real data goes.

A live league is that season real through `through` and simulated after it (services/sim_setup.py); the real
part loads with replay_pool.load_season_data(..., through=through). A future league is the season after the
newest one (`future_season`), simulated whole.
"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import NflGame, PlayerStatsWeekly, TeamGameStats
from services.projections import REGULAR_SEASON
from services.replay_pool import SUPPORTED_SEASONS


def newest_season(session: Session) -> int | None:
    """The newest season with regular-season player stats."""
    return session.scalar(select(func.max(PlayerStatsWeekly.season)).where(
        PlayerStatsWeekly.season_type == REGULAR_SEASON))


def _loaded_weeks(session: Session, model, season: int, *where) -> set[int]:
    return set(session.scalars(select(model.week).distinct().where(model.season == season, *where)))


def current_live(session: Session) -> tuple[int, int] | None:
    """(season, through) for the season in progress: the newest season with stats, real through the last week
    up to which every scheduled game has a final score and player and team stats are loaded. None when no
    season is in progress (all of it is played, or not even week 1)."""
    season = newest_season(session)
    if season is None:
        return None
    games = session.execute(select(NflGame.week, NflGame.home_score, NflGame.away_score).where(
        NflGame.season == season, NflGame.game_type == REGULAR_SEASON)).all()
    if not games:
        return None
    last_week = max(week for week, _, _ in games)
    unfinished = {week for week, home, away in games if home is None or away is None}
    stats = (_loaded_weeks(session, PlayerStatsWeekly, season, PlayerStatsWeekly.season_type == REGULAR_SEASON)
             & _loaded_weeks(session, TeamGameStats, season))
    through = 0
    while through < last_week and through + 1 not in unfinished and through + 1 in stats:
        through += 1
    return (season, through) if 1 <= through < last_week else None


def future_season(session: Session) -> int:
    """The season after the newest one (never a supported past season)."""
    return max(newest_season(session) or 0, SUPPORTED_SEASONS[-1]) + 1
