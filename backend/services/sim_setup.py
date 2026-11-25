"""Simulated Replay seasons: what a new live or future league is built from. The ONLY simulated-season module
that reads the database; the engine (services/sim_season, sim_world, sim_market) is pure.

- live: the season in progress (replay_live.current_live), real through week N, weeks N+1.. simulated from the
  league's own seed. The base is loaded real through week N only, so week N+1's injury report and expert ranks
  are simulated from what is known through N (the real ones, published during the week, are never read). A
  league may start "now" (Settings.start_week = N + 1): it drafts on today's market ADP (`_market_adp`: the
  belief before week N + 1 of its own simulated season, the one that week's simulated expert snapshot came
  from), in the league's format.
- future: the season after the newest one, simulated whole from what is known today (`_future_base`): the
  belief at the end of the newest season so far (the season in progress through its last finished week, else
  the finished one; estimated on the belief at the end of the season before it, `_season_end`, so a star's
  longer record counts), carried through the offseason (sim_next_year, with sim_data's rookies and moves), on
  the stored schedule or a generated one (sim_schedule, seeded by the season, so every league shares it). Its
  ADP is the market's in the league's format, leaning on the newest season's track record.

The season is built once, when its league is created, and frozen into the save (services/replay_blob.py).
"""
import logging
from dataclasses import replace

from sqlalchemy.orm import Session

from services import replay_blob, replay_live, replay_pool, replay_state as rs
from services.replay_pool import SeasonData
from services.replay_state import Settings
from services.valuation import NoDataError

log = logging.getLogger(__name__)

REPLAY, LIVE, FUTURE = "replay", "live", "future"
MIN_WEEKS_LEFT = 3                  # regular-season weeks a league that starts now must still have
NOW_PLAYOFF_TEAMS = 4               # a league that starts now: the smallest bracket, the longest regular season
DEFAULT_CHAMPIONSHIP = 17           # api/replay_routes.CreateBody.playoff_end_week's default


def kind_of(session: Session, season: int) -> tuple[str, int]:
    """(kind, last real week) of a season a league may be created on: (REPLAY, 0) for a complete season
    (replay_live.replay_seasons), (LIVE, through) for the season in progress, (FUTURE, 0) for the one after the
    newest started one. ValueError otherwise."""
    year = replay_live.year(session)
    live, seasons, future = year.live, year.seasons, year.future
    if live and live[0] == season:
        return LIVE, live[1]
    if season in seasons:
        return REPLAY, 0
    if season == future:
        return FUTURE, 0
    simulated = f"{live[0]} (in progress) and {future}" if live else str(future)
    raise ValueError(f"Replay seasons run from {seasons[0]} to {seasons[-1]}, plus {simulated} simulated; "
                     f"not {season}.")


def now_window(through: int, championship_week: int, playoff_teams: int) -> tuple[int, int]:
    """(regular_season_weeks, trade_deadline_week) of a league starting in week through + 1: the deadline two
    thirds of the way through what is left. ValueError when fewer than MIN_WEEKS_LEFT regular-season weeks remain."""
    start, regular = through + 1, championship_week - rs.playoff_rounds(playoff_teams)
    left = regular - start + 1
    if left < MIN_WEEKS_LEFT:
        raise ValueError(f"Only {max(left, 0)} regular-season weeks are left after week {through}, and a league that "
                         f"starts now needs {MIN_WEEKS_LEFT}: pick a later championship week or fewer playoff teams.")
    return regular, start + (2 * left) // 3 - 1


def build(session: Session, settings: Settings, seed: int) -> tuple[bytes, SeasonData]:
    """The whole season a new simulated league plays (real weeks before settings.sim_from, its own simulated
    future after), to be frozen with it: (its save's bytes, the season as the save holds it; replay_blob.freeze).
    NoDataError when the season's data is not ready."""
    from services import sim_next_year, sim_season      # the engine, imported only when a league is built

    if settings.sim_from is None:
        raise ValueError(f"{settings.season} is not a simulated season.")
    if settings.sim_from == 1:
        base, world = _future_base(session, settings.season, settings)
        rookies = [pid for pid in world.players if pid >= sim_next_year.ROOKIE_ID_BASE]     # this draft class
        data = sim_season.simulate(base, seed, 1, prior=world, rookies=rookies)
    else:
        base = replay_pool.load_season_data(session, settings.season, settings.scoring, settings.scoring_rules,
                                            settings.adp_format, through=settings.sim_from - 1)
        rookies = [pid for pid, first in first_seasons(session).items() if first == settings.season]
        data = sim_season.simulate(base, seed, settings.sim_from, rookies=rookies)
        data = _market_adp(data, settings) if settings.start_week > 1 else data
    return replay_blob.freeze(data)


def _market_adp(data: SeasonData, settings: Settings) -> SeasonData:
    """Every player's ADP replaced by the market's going into the league's first week, in the league's format
    (settings.adp_format), so a draft held mid-season reflects today, not August. The belief reads the
    simulated season as known before that week, which includes that week's injury tags (a starter's running
    IR stint): the same belief the week's expert snapshot was drawn from, so the draft room's rank and ADP agree."""
    from services import sim_next_year, sim_world

    week = settings.start_week
    return sim_next_year.with_market_adp(replace(data, adp_format=settings.adp_format),
                                         sim_world.estimate(data, week), data.last_week - week + 1)


# ---------------- the future season ----------------

def source(session: Session, year: replay_live.Year | None = None) -> tuple[int, int | None] | None:
    """(season, through) the future season is built from: the newest started season (replay_live.newest_started)
    through its last finished week while in progress, else in full (through None). None when there is none.
    `year`: the request's replay_live.year, when it has read it already."""
    found = (year or replay_live.year(session)).newest
    if found is None:
        return None
    season, through, last_week = found
    return season, (through if through < last_week else None)


def future_problem(session: Session, season: int, year: replay_live.Year | None = None) -> str | None:
    """Why a league can't be created on the future season yet, or None when it can."""
    from services import sim_offseason

    found = source(session, year)
    if found is None:
        return "No season is loaded yet: prepare one first."
    newest, through = found
    if not replay_pool.readiness(session, newest, through)["ready"]:
        return f"Prepare {newest} first: the {season} season is built from it."
    if not sim_offseason.path(season).exists():          # sim_offseason.load would read it as no rookies, no moves
        return f"{sim_offseason.path(season).name} is missing: add it first."
    try:
        sim_offseason.load(season)
    except ValueError as exc:
        return f"{sim_offseason.path(season).name}: {exc}"
    return None


def first_seasons(session: Session) -> dict[int, int]:
    """{player id: first season with regular-season stats or kicks in the database}."""
    from sqlalchemy import func, select

    from database.models import KickerGame, PlayerStatsWeekly

    found: dict[int, int] = {}
    for model, *where in ((PlayerStatsWeekly, PlayerStatsWeekly.season_type == "REG"), (KickerGame,)):
        for pid, first in session.execute(select(model.player_id, func.min(model.season)).where(*where)
                                          .group_by(model.player_id)):
            found[pid] = min(first, found.get(pid, first))
    return found


def _schedule(session: Session, season: int, standings_from: SeasonData):
    """The real regular-season schedule when the database has it, else a generated one (sim_schedule) from the
    newest season's standings so far, seeded by the season number: every league on that season plays the same
    schedule, as real leagues do (it only changes while the newest season's standings do)."""
    from services import sim_schedule
    from services.schedule_strength import load_schedule

    real = load_schedule(session, season)
    if real.games:
        return replace(real, games=tuple(replace(g, home_score=None, away_score=None) for g in real.games))
    played = load_schedule(session, standings_from.season)
    return sim_schedule.build(season, sim_schedule.standings_now(played.games), seed=season)


def _defense_prior(data: SeasonData) -> dict[tuple[str, str], float]:
    """Next season's defense_prior (replay_defense.load_prior's shape) from this season's loaded games: the same
    solve, without reading the database again."""
    from services import replay_defense

    solved, averages = replay_defense._solve_all(replay_defense._played_points(data))
    return {**{key: round(factor, 4) for key, (factor, _) in solved.items()},
            **{(replay_defense.AVERAGE, position): round(value, 2) for position, value in averages.items()}}


def _season_end(session: Session, season: int, settings: Settings):
    """The belief at the end of a complete `season` (the prior the newest season is estimated on: a preseason
    market leans on more than one season), or None when the database doesn't have it."""
    from services import sim_world

    if season not in replay_live.replay_seasons(session):
        return None
    try:
        data = replay_pool.load_season_data(session, season, settings.scoring, settings.scoring_rules,
                                            settings.adp_format)
    except NoDataError:
        return None
    return sim_world.estimate(data, data.last_week + 1)


def _future_base(session: Session, season: int, settings: Settings) -> tuple[SeasonData, object]:
    """(the season to simulate: every player of the new belief, the schedule, preseason ADP and week-1
    projections, no games; the prior World) for the season after the newest one, from what is known today
    (sim_next_year). Raises NoDataError when it can't be built (future_problem says why)."""
    from services import sim_market, sim_next_year, sim_offseason, sim_world

    problem = future_problem(session, season)
    if problem:
        raise NoDataError(problem)
    newest, through = source(session)
    data = replay_pool.load_season_data(session, newest, settings.scoring, settings.scoring_rules,
                                        settings.adp_format, through=through)
    world = sim_world.estimate(data, (through or data.last_week) + 1,
                               _season_end(session, newest - 1, settings))
    rookies, moves = sim_offseason.load(season)
    matched, missing = sim_offseason.match(moves, data.players)
    if missing:
        log.warning("Offseason %s: no player matches %s", season, ", ".join(missing))
    # shortcut: experience counts seasons since a player's first database season (2018 on), so a veteran from
    # before 2018 looks younger than he is until the cap bucket; store birth dates or draft years to fix it.
    experience = {pid: newest - first + 1 for pid, first in first_seasons(session).items() if first <= newest}
    drafted = sim_next_year.rookie_ids(season, rookies)
    belief, pool = sim_next_year.next_year(world, data.players, experience, drafted,
                                           {matched[m.name]: m.team for m in moves if m.name in matched},
                                           sim_next_year.starts_by_team(data))
    prior = _defense_prior(data)
    base = sim_next_year.season_base(season, belief, pool, _schedule(session, season, data), settings.scoring,
                                     settings.scoring_rules, prior, settings.adp_format, sim_market.track_record(data),
                                     sim_next_year.rookie_premium(drafted))
    return base, belief


# ---------------- the seasons list ----------------

def simulated(session: Session, year: replay_live.Year | None = None) -> list[dict]:
    """GET /seasons' `simulated` rows (shape at the top of services/replay_views.py). `year`: as `source`."""
    rows = []
    year = year or replay_live.year(session)
    live = year.live
    if live:
        season, through = live
        ready = replay_pool.readiness(session, season, through)["ready"]
        try:
            now_window(through, DEFAULT_CHAMPIONSHIP, NOW_PLAYOFF_TEAMS)
            late = None
        except ValueError as exc:
            late = str(exc)
        rows.append({"season": season, "kind": LIVE, "through": through, "ready": ready,
                     "can_start_now": ready and late is None,
                     "reason": None if ready else f"Prepare {season} first: weeks 1-{through} are not all imported."})
    future = year.future
    problem = future_problem(session, future, year)
    rows.append({"season": future, "kind": FUTURE, "through": 0, "ready": problem is None, "can_start_now": False,
                 "reason": problem})
    return rows
