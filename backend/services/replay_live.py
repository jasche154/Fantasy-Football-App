"""Replay League: which seasons the database holds, and where each stands. No code edit when a season ends.

The year, as the database sees it (S = the newest season whose week 1 is final, `newest_started`):
- S in progress (weeks 1..N final, N < its last week): S is LIVE through N (`current_live`); a live league plays
  it real through N, simulated after (services/sim_setup.py); S + 1 is the FUTURE season, simulated whole.
- S complete (every week final): S is an ordinary replay season (`replay_seasons` = 2018..S) once it is prepared
  again for its last weeks; S + 1 is the future season. Its schedule arriving, or stats of week 1 games in
  progress, change nothing: a season with no final week is not started.
- S + 1's week 1 final: S + 1 is live, S + 2 the future season.
A season later than this calendar year is ignored (a bad feed's season 2999 can't have a final week yet).

A week is final when player stats are loaded for it and every game has a score and team stats for both teams,
except a game that was not played (cancelled, like BUF@CIN in 2022). A game without a score is only treated as
not played once a later game of the season (postseason included), at least STALE_DAYS after it and not after
today, has its score: that score proves the schedule was read again well after the game, so a failed download,
a partial reload or an idle app never makes a played game look cancelled. A cancelled game in the last regular
week therefore holds the season back until a postseason game has its score (about a week). `today` is a
function so tests can pin the calendar.

`year(session)` reads all of it once (`Year`), for a request that needs several of the answers.
"""
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import NflGame, PlayerStatsWeekly, TeamGameStats
from services.projections import REGULAR_SEASON

FIRST_SEASON = 2018                         # ESPN and Sleeper have no pre-game projections before it
SUPPORTED_SEASONS = range(FIRST_SEASON, 2026)   # complete when this was written; replay_seasons() grows past it
STALE_DAYS = 4                              # a game without a score this long after its date was not played


def today() -> date:
    return date.today()


def _last_scored(session: Session, season: int) -> date | None:
    """The date of the season's latest game with a score, postseason included; None when no game has one, or
    when that date can't be read (the feed's `gameday` is free text): unscored games then keep holding their
    weeks back, the safe side."""
    day = session.scalar(select(func.max(NflGame.gameday)).where(
        NflGame.season == season, NflGame.home_score.is_not(None), NflGame.away_score.is_not(None)))
    try:
        return date.fromisoformat(day[:10]) if day else None
    except ValueError:
        return None


def _holds_back(game, gone: str, teams: set[tuple[int, str]]) -> bool:
    """Whether a game keeps its week from being final: scored without both teams' stats, or unscored and not dated
    on or before `gone` (the last date a game not played may have; "" when no game has a score)."""
    week, home, away, home_score, away_score, day = game
    if home_score is None or away_score is None:
        return not (day and day[:10] <= gone)
    return not {(week, home), (week, away)} <= teams


def progress(session: Session, season: int) -> tuple[int, int]:
    """(through, last week) of a season: the last week up to which every week is final (see the module
    docstring), and its last regular-season week (0, 0 without a schedule)."""
    games = session.execute(select(NflGame.week, NflGame.home_team, NflGame.away_team, NflGame.home_score,
                                   NflGame.away_score, NflGame.gameday).where(
        NflGame.season == season, NflGame.game_type == REGULAR_SEASON)).all()
    if not games:
        return 0, 0
    seen = _last_scored(session, season)
    gone = (min(today(), seen) - timedelta(days=STALE_DAYS)).isoformat() if seen else ""
    teams = {(week, team) for week, team in session.execute(
        select(TeamGameStats.week, TeamGameStats.team).where(TeamGameStats.season == season))}
    # every regular-season week is below the postseason's, so no season_type filter (the index covers this)
    stats = set(session.scalars(select(PlayerStatsWeekly.week).distinct().where(PlayerStatsWeekly.season == season)))
    unfinished = {game.week for game in games if _holds_back(game, gone, teams)}
    last_week = max(game.week for game in games)
    through = 0
    while through < last_week and through + 1 not in unfinished and through + 1 in stats:
        through += 1
    return through, last_week


def newest_started(session: Session) -> tuple[int, int, int] | None:
    """(season, through, last week) of the newest season with week 1 final, no later than this year; None when
    there is none. Walks down the seasons with a schedule (small tables), skipping any gap."""
    seasons = session.scalars(select(NflGame.season).distinct().where(NflGame.season <= today().year)
                              .order_by(NflGame.season.desc()))
    for season in seasons:
        through, last_week = progress(session, season)
        if through:
            return season, through, last_week
    return None


@dataclass(frozen=True)
class Year:
    """Where the seasons stand, from one `newest_started` read."""
    newest: tuple[int, int, int] | None     # (season, through, last week) of the newest started season

    @property
    def live(self) -> tuple[int, int] | None:
        """(season, through) for the season in progress; None when no season is (the newest one is complete,
        or the next one has not finished week 1)."""
        found = self.newest
        return (found[0], found[1]) if found and found[1] < found[2] else None

    @property
    def complete(self) -> int:
        """The newest complete season: the newest started one once every week is final, else the one before it
        (never before SUPPORTED_SEASONS' last, so an empty database still lists them)."""
        found = self.newest
        newest = found[0] - (found[1] < found[2]) if found else 0
        return max(newest, SUPPORTED_SEASONS[-1])

    @property
    def seasons(self) -> range:
        """The complete seasons a replay can be played on: FIRST_SEASON through the newest complete one."""
        return range(FIRST_SEASON, self.complete + 1)

    @property
    def future(self) -> int:
        """The season after the newest started one (never a complete past season)."""
        return max(self.newest[0] if self.newest else 0, self.complete) + 1


def year(session: Session) -> Year:
    return Year(newest_started(session))


def current_live(session: Session) -> tuple[int, int] | None:
    """Year.live, read now."""
    return year(session).live


def complete_season(session: Session) -> int:
    """Year.complete, read now."""
    return year(session).complete


def replay_seasons(session: Session) -> range:
    """Year.seasons, read now."""
    return year(session).seasons


def future_season(session: Session) -> int:
    """Year.future, read now."""
    return year(session).future
