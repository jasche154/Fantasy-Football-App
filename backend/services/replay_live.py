"""Replay League: which seasons the database holds, and where each stands. No code edit when a season ends.

The year, as the database sees it (S = the newest season whose week 1 is final, `newest_started`):
- S in progress (weeks 1..N final, N < its last week): S is LIVE through N (`current_live`); a live league plays
  it real through N, simulated after (services/sim_setup.py); S + 1 is the FUTURE season, simulated whole.
- S complete (every week final): S is an ordinary replay season (`replay_seasons` = 2018..S) once it is prepared
  again for its last weeks; S + 1 is the future season. Its schedule arriving, or stats of week 1 games in
  progress, change nothing: a season with no final week is not started.
- S + 1's week 1 final: S + 1 is live, S + 2 the future season.

A week is final when its player and team stats are loaded and every game has a score, except a game still
without one STALE_DAYS after its date: it was not played (cancelled, like BUF@CIN in 2022), so it never holds
its week, or the season, back. `today` is a function so tests can pin the calendar.
"""
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import NflGame, PlayerStatsWeekly, TeamGameStats
from services.projections import REGULAR_SEASON

FIRST_SEASON = 2018                         # ESPN and Sleeper have no pre-game projections before it
SUPPORTED_SEASONS = range(FIRST_SEASON, 2026)   # complete when this was written; replay_seasons() grows past it
STALE_DAYS = 4                              # a game without a score this long after its date was not played


def today() -> date:
    return date.today()


def _loaded_weeks(session: Session, model, season: int, *where) -> set[int]:
    return set(session.scalars(select(model.week).distinct().where(model.season == season, *where)))


def progress(session: Session, season: int) -> tuple[int, int]:
    """(through, last week) of a season: the last week up to which every week is final (see the module
    docstring), and its last regular-season week (0, 0 without a schedule)."""
    games = session.execute(select(NflGame.week, NflGame.home_score, NflGame.away_score, NflGame.gameday).where(
        NflGame.season == season, NflGame.game_type == REGULAR_SEASON)).all()
    if not games:
        return 0, 0
    gone = (today() - timedelta(days=STALE_DAYS)).isoformat()
    last_week = max(game.week for game in games)
    unfinished = {week for week, home, away, day in games
                  if (home is None or away is None) and not (day and day[:10] < gone)}
    stats = (_loaded_weeks(session, PlayerStatsWeekly, season, PlayerStatsWeekly.season_type == REGULAR_SEASON)
             & _loaded_weeks(session, TeamGameStats, season))
    through = 0
    while through < last_week and through + 1 not in unfinished and through + 1 in stats:
        through += 1
    return through, last_week


def newest_started(session: Session) -> tuple[int, int, int] | None:
    """(season, through, last week) of the newest season with week 1 final; None when there is none."""
    seasons = session.scalars(select(PlayerStatsWeekly.season).distinct().where(
        PlayerStatsWeekly.season_type == REGULAR_SEASON).order_by(PlayerStatsWeekly.season.desc()))
    for season in seasons:
        through, last_week = progress(session, season)
        if through:
            return season, through, last_week
    return None


def current_live(session: Session) -> tuple[int, int] | None:
    """(season, through) for the season in progress; None when no season is (the newest one is complete, or the
    next one has not finished week 1)."""
    found = newest_started(session)
    return (found[0], found[1]) if found and found[1] < found[2] else None


def complete_season(session: Session) -> int:
    """The newest complete season: the newest started one once every week is final, else the one before it
    (never before SUPPORTED_SEASONS' last, so an empty database still lists them)."""
    found = newest_started(session)
    newest = found[0] - (found[1] < found[2]) if found else 0
    return max(newest, SUPPORTED_SEASONS[-1])


def replay_seasons(session: Session) -> range:
    """The complete seasons a replay can be played on: FIRST_SEASON through the newest complete one."""
    return range(FIRST_SEASON, complete_season(session) + 1)


def future_season(session: Session) -> int:
    """The season after the newest started one (never a complete past season)."""
    found = newest_started(session)
    return max(found[0] if found else 0, complete_season(session)) + 1
