"""Simulated Replay seasons: what a new live or future league is built from. The ONLY simulated-season module
that reads the database; the engine (services/sim_season, sim_world, sim_market) is pure.

- live: the season in progress (replay_live.current_live), real through week N, weeks N+1.. simulated from the
  league's own seed. The base is loaded real through week N only, so week N+1's injury report and expert ranks
  are simulated from what is known through N (the real ones, published during the week, are never read), except
  that each player's Sleeper status at creation seeds week N+1's hard tags (`with_status_now`). A
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
from datetime import datetime
from dataclasses import replace
from types import MappingProxyType
from collections.abc import Mapping
from typing import NamedTuple

from sqlalchemy.orm import Session

from services import replay_blob, replay_live, replay_pool, replay_state as rs
from services.replay_pool import SeasonData
from services.replay_state import Settings
from services.valuation import NoDataError

log = logging.getLogger(__name__)
_per_data_cache: dict[tuple[int, str], tuple[object, object]] = {}   # (id(data), name) -> (data, value): see _per_data
PER_DATA_ENTRIES = 16


def _per_data(data: SeasonData, name: str, compute):
    """compute(), once per replay_pool season object (`data`, as load_season_data caches it: new data in the
    database is a new object) and `name`: the parts of a league's season that every seed shares. Emptied by
    replay_pool.clear_cache."""
    hit = _per_data_cache.get((id(data), name))
    if hit is not None and hit[0] is data:
        return hit[1]
    value = compute()
    if len(_per_data_cache) >= PER_DATA_ENTRIES:
        _per_data_cache.clear()
    _per_data_cache[(id(data), name)] = (data, value)
    return value


replay_pool.CLEAR_HOOKS.append(_per_data_cache.clear)
replay_pool.EVICT_HOOKS.append(_per_data_cache.clear)   # it keys on season objects too

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
        base, world, carried, qbs, teams = _future_base(session, settings.season, settings, seed)
        rookies = [pid for pid in world.players if pid >= sim_next_year.ROOKIE_ID_BASE]     # this draft class
        data = sim_season.simulate(base, seed, 1, prior=world, carried=carried, rookies=rookies, qbs=qbs,
                                   team_prior=teams)
    else:
        base = replay_pool.load_season_data(session, settings.season, settings.scoring, settings.scoring_rules,
                                            settings.adp_format, through=settings.sim_from - 1)
        first = _per_data(base, "first seasons", lambda: MappingProxyType(first_seasons(session)))
        rookies = [pid for pid, season in first.items() if season == settings.season]
        data = sim_season.simulate(with_status_now(session, base, settings.sim_from), seed, settings.sim_from,
                                   rookies=rookies, qbs=qb_inputs(session, settings.season),
                                   team_prior=team_prior(session, settings.season),
                                   posted=posted_lines(base, settings.sim_from))
        data = _market_adp(data, settings) if settings.start_week > 1 else data
    return replay_blob.freeze(data)


def final_scores(session: Session, season: int) -> list[tuple[int, str, str, int, int]]:
    """(week, home, away, home points, away points) of `season`'s finished games in the database."""
    from services.schedule_strength import load_schedule

    return [(g.week, g.home_team, g.away_team, g.home_score, g.away_score)
            for g in load_schedule(session, season).games if g.is_final]


def team_prior(session: Session, season: int) -> dict:
    """{team: sim_lines.Rating}: the points ratings `season`'s games start from, last season's final ones
    regressed (sim_lines.carry_over), from its real scores in the database (none: {})."""
    from services import sim_lines

    scores = final_scores(session, season - 1)
    return sim_lines.carry_over(scores) if scores else {}


def posted_lines(base: SeasonData, from_week: int) -> dict[tuple[str, int], tuple[float | None, float | None]]:
    """{(home team, from_week): (spread, total)} of the real lines already posted for the first simulated week
    (either may be None): its games use them as their prediction (sim_lines). Later weeks' posted lines are not
    passed: they know nothing of the simulated weeks before them (a starter hurt in a simulated game)."""
    return {(g.home_team, g.week): (g.spread_line, g.total_line) for g in base.schedule.games
            if g.week == from_week and (g.spread_line is not None or g.total_line is not None)}


def _market_adp(data: SeasonData, settings: Settings) -> SeasonData:
    """Every player's ADP replaced by the market's going into the league's first week, in the league's format
    (settings.adp_format), so a draft held mid-season reflects today, not August. The belief reads the
    simulated season as known before that week, which includes that week's injury tags (a starter's running
    IR stint): the same belief the week's expert snapshot was drawn from, so the draft room's rank and ADP agree."""
    from services import sim_next_year, sim_world

    week = settings.start_week
    return sim_next_year.with_market_adp(replace(data, adp_format=settings.adp_format),
                                         sim_world.estimate(data, week), data.last_week - week + 1)


def with_status_now(session: Session, base: SeasonData, week: int) -> SeasonData:
    """`base` with week `week`'s tags (the first simulated week of a live league) from each player's current
    Sleeper status, read once when the league is created: public news of today, so no hindsight, and never for a
    real week. Without it a player hurt in the last real game (Drake Maye, 2026 week 10) starts the simulation
    healthy. Out / Doubtful / IR / PUP / suspended become a hard tag that sim_season carries as an open absence
    (its length drawn by the injury model); Questionable and healthy are left to the model. A player already
    hard-tagged in the last real week keeps that streak, and an Out / Doubtful status older than availability's
    freshness window is not news of this week (skipped, counted in the log); IR / PUP / suspended last weeks, so
    they count however old the sync is, unless he played a game that kicked off after it was fetched (activated
    since, also during a bye week). Most statuses stale means the Sleeper sync has stopped: logged at WARNING."""
    from datetime import timedelta, timezone

    from sqlalchemy import select

    from database.models import PlayerStatus
    from integrations.weekly_sources import utc
    from services.availability import SLEEPER_FRESH_HOURS
    from services.replay_injuries import MAX_TEXT
    from services.replay_pool import Injury

    cutoff = datetime.now(timezone.utc) - timedelta(hours=SLEEPER_FRESH_HOURS)
    added, stale, read = {}, 0, 0
    for pid, status, body, fetched in session.execute(select(
            PlayerStatus.player_id, PlayerStatus.injury_status, PlayerStatus.injury_body_part,
            PlayerStatus.fetched_at).where(PlayerStatus.injury_status.is_not(None))):
        tag = SLEEPER_TAGS.get(status.upper())
        last = base.injuries.get((pid, week - 1))
        if tag is None or pid not in base.players or (pid, week) in base.injuries or (last and last.hard):
            continue
        read += 1
        if utc(fetched) < cutoff and (tag not in LONG_TAGS or _played_since(base, pid, week, utc(fetched))):
            stale += 1
            continue
        added[(pid, week)] = Injury(tag, None, (body or "").strip()[:MAX_TEXT] or None)
    if stale:
        level = logging.WARNING if stale * 2 > read else logging.INFO
        log.log(level, "Week %s status: %s of %s statuses older than %sh skipped", week, stale, read, SLEEPER_FRESH_HOURS)
    return replace(base, injuries=MappingProxyType({**base.injuries, **added})) if added else base


def _played_since(base: SeasonData, pid: int, week: int, fetched: datetime) -> bool:
    """He played a game before `week` that kicked off after the status was fetched (activated since, a bye week
    after it or not). A tag fetched after a game's kickoff (hurt Sunday, IR Tuesday) is newer than that game; a
    week - 1 game with no known kickoff counts as after the sync, an older one as before it."""
    for w in range(1, week):
        if (pid, w) not in base.actual and (pid, w) not in base.box:
            continue
        game = base.schedule.game(base.team_at(pid, w + 1), w)
        kickoff = game.kickoff if game else None
        if (kickoff is None and w == week - 1) or (kickoff is not None and fetched < kickoff):
            return True
    return False


# Sleeper injury_status (upper-cased) -> the replay tag that week's absence starts from (services/availability's
# SLEEPER_ZERO plus Doubtful, which plays about 3% of the time)
SLEEPER_TAGS = {"OUT": "O", "DOUBTFUL": "O", "DNR": "O", "NA": "O", "COV": "O", "IR": "IR", "IR-R": "IR",
                "NFI": "IR", "PUP": "PUP", "SUS": "SUS"}
LONG_TAGS = {"IR", "PUP", "SUS"}     # absences of weeks: still news when the status sync is a few days old


def qb_inputs(session: Session, season: int, picks: list[dict] | None = None):
    """sim_qb_jobs.QbInputs for `season`: the QBs whose real preseason QB ADP rank (the "ppr" list, 2018 on) was
    sim_qb.PAST_RANK or better in one of the sim_qb.PAST_SEASONS seasons before, and this season's drafted QBs
    with their picks (nflverse rows `picks`, else the cached download only: building a league never downloads;
    without the cache the live season has no rookie takeovers, logged)."""
    from sqlalchemy import select

    from database.models import Player, ReplayAdp
    from services import sim_draft_picks, sim_qb, sim_qb_jobs

    past: set[int] = set()
    for year in range(season - sim_qb.PAST_SEASONS, season):
        listed = session.execute(select(ReplayAdp.player_id).join(Player, Player.id == ReplayAdp.player_id)
                                 .where(ReplayAdp.season == year, ReplayAdp.format == "ppr", Player.position == "QB")
                                 .order_by(ReplayAdp.adp, ReplayAdp.player_id).limit(sim_qb.PAST_RANK)).scalars()
        past.update(listed)
    if picks is None:
        cached = sim_draft_picks._cached_picks()
        picks = cached.get("rows", [])
        if not any(r.get("season") == season for r in picks):
            log.info("No cached nflverse draft for %s: its rookie QBs take no job from a starter", season)
        elif season not in sim_draft_picks._settled(cached):        # a summer download: some gsis ids still empty
            log.warning("The cached nflverse draft for %s was downloaded before it settled: rookie QBs without an id "
                        "take no job from a starter (download again with --refresh)", season)
    mine = {r["gsis_id"]: r["pick"] for r in picks if r.get("season") == season and r.get("position") == "QB"
            and r.get("gsis_id")}
    ids = dict(session.execute(select(Player.gsis_id, Player.id).where(Player.gsis_id.in_(list(mine)))).all())
    return sim_qb_jobs.QbInputs(frozenset(past), MappingProxyType({ids[g]: pick for g, pick in mine.items() if g in ids}))


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


class Future(NamedTuple):
    base: SeasonData             # every player of the new belief, the schedule, preseason ADP, week-1 projections
    belief: object               # the prior World (sim_world.World)
    carried: dict                # {player: sim_injuries.Spell}: the truth's absences running into week 1
    qbs: object = None           # sim_qb_jobs.QbInputs: the QB jobs' inputs (the seeded draft's rookie QB picks)
    teams: Mapping = MappingProxyType({})   # {team: sim_lines.Rating}: the newest season's team ratings, carried over


def injury_detail(session: Session) -> dict[int, tuple[str | None, str | None]]:
    """{player: (body detail, notes)} of the current injury statuses (e.g. ("Knee - ACL", "Surgery"))."""
    from sqlalchemy import select

    from database.models import PlayerStatus

    rows = session.execute(select(PlayerStatus.player_id, PlayerStatus.injury_body_part, PlayerStatus.injury_notes)
                           .where(PlayerStatus.injury_body_part.is_not(None)))
    return {pid: (body, notes) for pid, body, notes in rows}


def _future_base(session: Session, season: int, settings: Settings, seed: int = 0) -> Future:
    """The season after the newest one, from what is known today plus the league's `seed` (its draft:
    sim_rookie_draft; its carried injuries: sim_carryover), through sim_next_year. Raises NoDataError when it
    can't be built (future_problem says why)."""
    from services import (sim_carryover, sim_lines, sim_market, sim_next_year, sim_offseason, sim_rookie_draft,
                          sim_world)

    problem = future_problem(session, season)
    if problem:
        raise NoDataError(problem)
    newest, through = source(session)
    data = replay_pool.load_season_data(session, newest, settings.scoring, settings.scoring_rules,
                                        settings.adp_format, through=through)
    world = _per_data(data, "belief", lambda: sim_world.estimate(data, (through or data.last_week) + 1,
                                                                 _season_end(session, newest - 1, settings)))
    rookies, moves = sim_offseason.load(season)
    matched, missing = sim_offseason.match(moves, data.players)
    if missing:
        log.warning("Offseason %s: no player matches %s", season, ", ".join(missing))
    # shortcut: experience counts seasons since a player's first database season (2018 on), so a veteran from
    # before 2018 looks younger than he is until the cap bucket; store birth dates or draft years to fix it.
    first = _per_data(data, "first seasons", lambda: MappingProxyType(first_seasons(session)))
    experience = {pid: newest - season + 1 for pid, season in first.items() if season <= newest}
    moved = {matched[m.name]: m.team for m in moves if m.name in matched}
    # the pool's schedule carries no scores (results live in data.scores): the draft order needs them back
    played = tuple(replace(g, home_score=data.scores.get((g.home_team, g.week)),
                           away_score=data.scores.get((g.away_team, g.week))) for g in data.schedule.games)
    starts = sim_next_year.starts_by_team(data)
    by_name = {r.name: r for r in sim_rookie_draft.run(rookies, played, world, moved, seed, starts)}
    drafted = {pid: by_name[r.name] for pid, r in sim_next_year.rookie_ids(season, rookies).items()}  # ids: file picks
    qbs = replace(qb_inputs(session, season, []), picks=MappingProxyType(
        {pid: r.pick for pid, r in drafted.items() if r.position == "QB"}))      # the seeded draft's picks
    record = _per_data(data, "record", lambda: sim_market.track_record(data))
    belief, pool = sim_next_year.next_year(world, data.players, experience, drafted, moved, starts, qbs.past, record)
    schedule = _schedule(session, season, data)
    start = sim_carryover.next_week_one(data, schedule)
    carries = {pid: c for pid, c in sim_carryover.carried(data, start, injury_detail(session)).items()
               if pid in belief.players} if start else {}
    belief = sim_carryover.with_expected(belief, carries)
    base = sim_next_year.season_base(season, belief, pool, schedule, settings.scoring,
                                     settings.scoring_rules, _per_data(data, "defense", lambda: _defense_prior(data)),
                                     settings.adp_format, record,
                                     sim_next_year.rookie_premium(drafted))
    base = replace(base, notes=MappingProxyType({(pid, 0): sim_carryover.note(c) for pid, c in carries.items()}))
    scores = [(g.week, g.home_team, g.away_team, g.home_score, g.away_score) for g in played if g.is_final]
    return Future(base, belief, sim_carryover.draw(carries, seed), qbs,
                  sim_lines.carry_over(scores) if scores else {})


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
