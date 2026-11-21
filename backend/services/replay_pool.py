"""Replay League: one past season as plain data (player pool, real points, pre-game projections).

`load_season_data` is the only place the engine reads the database. Everything it returns is immutable and
point-in-time friendly: `actual` holds what really happened (used to score a week once it is played),
`projected` holds what a reputable site said BEFORE each game (ESPN, with Sleeper filling the gaps), and
`team_at` gives the team a player was last seen on before a week, never his team today. `players` is the
whole season's pool; `as_of(week)` narrows it to those known by then (an ADP, a projection or a game), so
a player who only turns up in week 9 is not on the draft board or the wire before week 9. `injuries` holds
each week's tag (that week's final report or roster status, known before its games); none at draft time.
`usage` holds each skill game's snap share, targets, target share and carries, `box` its box score (results,
like `actual`); `defense_prior` is last season's defense factors (replay_defense.load_prior, not a result).
`expert_ranks` / `expert_age` are FantasyPros position ranks as of each week (services/replay_experts.py) and
`returns` says when each hard-tag streak really ended (`hard_tag_returns`).
"""
import logging
from collections import defaultdict
from collections.abc import Mapping
from datetime import date
from dataclasses import dataclass, field, replace
from functools import lru_cache
from threading import Lock
from types import MappingProxyType

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from database.models import (
    KickerGame, NflGame, Player, PlayerStatsWeekly, ReplayAdp, ReplayExpertRank, ReplayInjury, ReplayProjection,
    TeamGameStats,
)
from integrations.adp_history import TWO_WAY
from services import kdst_model, replay_box, replay_defense
from services.build_locks import key_lock
from services.kdst_rankings import DST_PREFIX
from services.projections import REGULAR_SEASON, load_game_lines
from services.replay_box import BoxLine
from services.schedule_strength import Schedule, load_schedule
from services.scoring import scoring_for
from services.valuation import NoDataError

log = logging.getLogger(__name__)

SUPPORTED_SEASONS = range(2018, 2026)       # ESPN and Sleeper have no pre-game projections before 2018
SKILL = ("QB", "RB", "WR", "TE")
ALL_POSITIONS = SKILL + ("K", "DST")
SOURCES = ("espn", "sleeper")               # in order of preference
MIN_ADP_PLAYERS = 180                       # a 12-team, 15-round draft
MIN_KICKERS = MIN_DEFENSES = 14             # one each for the largest league (ADP or a week-1 projection)

PlayerWeek = tuple[int, int]                # (player_id, week)

TAGS = ("Q", "D", "O", "IR", "PUP", "SUS")
HARD_TAGS = ("O", "IR", "PUP", "SUS")       # cannot play this week: forces `out`
IR_TAGS = ("O", "IR", "PUP")                # may go on IR (a suspension is not an injury)
PRACTICE = ("DNP", "LP", "FP")


@dataclass(frozen=True)
class Injury:
    """A player's tag for one week, known before that week's games (services/replay_injuries.py)."""
    tag: str                                # one of TAGS
    practice: str | None = None             # one of PRACTICE
    injury: str | None = None               # "Knee"; free text from the report, at most 60 characters

    @property
    def hard(self) -> bool:
        return self.tag in HARD_TAGS


@dataclass(frozen=True)
class Usage:
    """A skill player's role in one game (nflverse box score and snap counts)."""
    snap_pct: float | None                  # 0-100; None where snap counts are not loaded
    targets: int
    target_share: float | None              # 0-1 fraction of his team's targets
    carries: int


@dataclass(frozen=True)
class PoolPlayer:
    player_id: int
    name: str
    position: str
    adp: float | None = None
    adp_stdev: float | None = None
    adp_team: str | None = None             # the team stored with his ADP, only if it is a schedule team


@dataclass(frozen=True, eq=False)           # compared and hashed by identity: one object per loaded season
class SeasonData:
    season: int
    scoring: str                                              # "ppr" | "half" | "std"
    players: Mapping[int, PoolPlayer]                         # the whole pool
    schedule: Schedule                                        # use for byes and opponents only
    actual: Mapping[PlayerWeek, float]                        # real points; missing = did not play
    projected: Mapping[PlayerWeek, float]                     # pre-game projection; missing = none (bye, out)
    team_by_week: Mapping[int, tuple[tuple[int, str], ...]]   # player_id -> ((week, team) per game played), sorted
    last_week: int                                            # last regular-season week with games (17 or 18)
    known_before: int | None = None                           # set by as_of: nothing from this week's games on
    injuries: Mapping[PlayerWeek, Injury] = field(default_factory=lambda: MappingProxyType({}))   # tag per week
    usage: Mapping[PlayerWeek, Usage] = field(default_factory=lambda: MappingProxyType({}))      # skill games only
    expert_ranks: Mapping[PlayerWeek, int] = field(default_factory=lambda: MappingProxyType({}))  # expert pos rank; week 0 = preseason
    expert_age: Mapping[int, int] = field(default_factory=lambda: MappingProxyType({}))           # week -> snapshot age in days
    # (player, first week of a hard-tag streak) -> first later week with a real game, 0 = never. The one
    # sanctioned leak: replay_news turns it into a noisy report.
    returns: Mapping[PlayerWeek, int] = field(default_factory=lambda: MappingProxyType({}))
    box: Mapping[PlayerWeek, BoxLine] = field(default_factory=lambda: MappingProxyType({}))     # skill games only
    defense_prior: Mapping[tuple[str, str], float] = field(default_factory=lambda: MappingProxyType({}))  # last season

    def as_of(self, week: int) -> "SeasonData":
        """A copy holding only what was known before `week` was played (0 = draft time); cached.

        Real points and usage for weeks < week, pre-game projections for weeks <= week (week 1's at draft
        time: the preseason curve is built from them), injury tags for weeks 1..week (none at draft time), and each
        player's games before `week`, or else his first game of the season (the `team_at` fallback; an
        accepted limit: wrong only for a drafted player who changed teams before his first game). `players`
        holds only those who existed by then: an ADP, a projection or a real game in those weeks; it only
        grows with the week, so anyone rostered at week W is in every later copy. Give CPU logic and views
        this, never the full season."""
        if week < 0:
            raise ValueError(f"Week {week} is not a valid week.")
        if self.known_before is not None and self.known_before <= week:
            return self                                       # already holds no more than that
        return _as_of(self, week)

    def team_at(self, player_id: int, week: int) -> str | None:
        """His team as known before `week`: the team of his last game before it, else of his first game of
        the season, else the team stored with his ADP (a drafted player who never plays).

        The first-game fallback is an accepted limit: no stored source is point-in-time before a player's
        first game, so it is wrong only for a drafted player who changed teams before then."""
        games = self.team_by_week.get(player_id, ())
        team = None
        for game_week, game_team in games:
            if game_week >= week:
                break
            team = game_team
        if team is not None or games:
            return team if team is not None else games[0][1]
        player = self.players.get(player_id)
        return player.adp_team if player else None

    def on_bye(self, player_id: int, week: int) -> bool:
        """His team has no game that week. A player with no known team is never 'on bye' (he is just out)."""
        team = self.team_at(player_id, week)
        return team is not None and self.schedule.opponent(team, week) is None

    def projection(self, player_id: int, week: int) -> float:
        return self.projected.get((player_id, week)) or 0.0

    def points(self, player_id: int, week: int) -> float:
        return self.actual.get((player_id, week)) or 0.0


@lru_cache(maxsize=256)
def _as_of(data: SeasonData, week: int) -> SeasonData:
    actual = {key: points for key, points in data.actual.items() if key[1] < week}
    projected = {key: points for key, points in data.projected.items() if key[1] <= max(1, week)}
    seen = {pid for pid, _ in (*actual, *projected)}
    players = {pid: p for pid, p in data.players.items() if p.adp is not None or pid in seen}
    games = {pid: tuple(g for g in played if g[0] < week) or played[:1]
             for pid, played in data.team_by_week.items() if pid in players}
    return replace(
        data, known_before=week, players=MappingProxyType(players), team_by_week=MappingProxyType(games),
        actual=MappingProxyType({key: v for key, v in actual.items() if key[0] in players}),
        projected=MappingProxyType({key: v for key, v in projected.items() if key[0] in players}),
        injuries=MappingProxyType({key: v for key, v in data.injuries.items()
                                   if key[1] <= week and key[0] in players}),
        usage=MappingProxyType({key: v for key, v in data.usage.items() if key[1] < week and key[0] in players}),
        box=MappingProxyType({key: v for key, v in data.box.items() if key[1] < week and key[0] in players}),
        expert_ranks=MappingProxyType({key: v for key, v in data.expert_ranks.items() if key[1] <= week}),
        expert_age=MappingProxyType({w: v for w, v in data.expert_age.items() if w <= week}),
        returns=MappingProxyType({key: v for key, v in data.returns.items() if key[1] <= week}))


# ---------------- loading ----------------

_cache: dict[tuple, SeasonData] = {}
_cache_lock = Lock()


def clear_cache() -> None:
    """Forget loaded seasons (call after loading replay data, and between tests)."""
    with _cache_lock:
        _cache.clear()
    _as_of.cache_clear()


def load_season_data(session: Session, season: int, scoring: str) -> SeasonData:
    """The season as plain data, built once per (database, season, scoring).

    Raises ValueError for an unknown scoring and valuation.NoDataError when the season isn't ready."""
    scoring_for(scoring)
    key = (str(session.get_bind().url), season, scoring)
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached
    with key_lock(("replay_pool", key)):
        with _cache_lock:
            cached = _cache.get(key)                  # built by a request we waited for
        if cached is None:
            cached = _build(session, season, scoring)
            with _cache_lock:
                _cache[key] = cached
    return cached


def _count(session: Session, column, *where) -> int:
    return session.scalar(select(func.count(column)).where(*where)) or 0


def _schedule_teams(session: Session, season: int) -> set[str]:
    games = session.execute(select(NflGame.home_team, NflGame.away_team).where(
        NflGame.season == season, NflGame.game_type == REGULAR_SEASON))
    return {team for game in games for team in game}


def _draftable(session: Session, season: int, position: str) -> int:
    """Players at a position a draft can find: with an ADP or a week-1 projection."""
    drafted = select(ReplayAdp.player_id).where(ReplayAdp.season == season)
    projected = select(ReplayProjection.player_id).where(
        ReplayProjection.season == season, ReplayProjection.week == 1, ReplayProjection.pts_ppr > 0)
    return _count(session, Player.id, Player.position == position,
                  or_(Player.id.in_(drafted), Player.id.in_(projected)))


def readiness(session: Session, season: int) -> dict:
    """What is loaded for a season, and whether a game can be played on it."""
    teams = _schedule_teams(session, season)
    defenses = set(session.scalars(select(Player.gsis_id).where(Player.position == "DST")))
    has_stats = _count(session, PlayerStatsWeekly.id, PlayerStatsWeekly.season == season,
                       PlayerStatsWeekly.season_type == REGULAR_SEASON) > 0
    has_kdst = (_count(session, KickerGame.id, KickerGame.season == season) > 0
                and _count(session, TeamGameStats.id, TeamGameStats.season == season) > 0
                and all(f"{DST_PREFIX}{team}" in defenses for team in teams))     # every team's D/ST row exists
    adp_players = _count(session, ReplayAdp.id, ReplayAdp.season == season)
    weeks = set(session.scalars(select(ReplayProjection.week.distinct()).where(ReplayProjection.season == season)))
    last_week = session.scalar(select(func.max(NflGame.week)).where(
        NflGame.season == season, NflGame.game_type == REGULAR_SEASON)) or 0
    every_week = last_week > 0 and set(range(1, last_week + 1)) <= weeks    # a hole would zero a whole week
    kickers, defenses = _draftable(session, season, "K"), _draftable(session, season, "DST")
    ready = (has_stats and bool(teams) and has_kdst and adp_players >= MIN_ADP_PLAYERS
             and every_week and kickers >= MIN_KICKERS and defenses >= MIN_DEFENSES)
    projection_weeks = len(weeks)
    injury_weeks = _count(session, ReplayInjury.week.distinct(), ReplayInjury.season == season)  # not needed to play
    expert_snapshots = _count(session, ReplayExpertRank.scrape_date.distinct(), ReplayExpertRank.season == season)
    return {"season": season, "ready": ready, "has_stats": has_stats, "has_schedule": bool(teams),
            "has_kdst": has_kdst, "adp_players": adp_players, "projection_weeks": projection_weeks,
            "kickers": kickers, "defenses": defenses, "injury_weeks": injury_weeks,
            "expert_snapshots": expert_snapshots, "last_week": last_week}


def ready_last_weeks(session: Session) -> dict[int, int]:
    """{season: last week} for the supported seasons a game can be started on, oldest first."""
    rows = (readiness(session, season) for season in SUPPORTED_SEASONS)
    return {row["season"]: row["last_week"] for row in rows if row["ready"]}


def ready_seasons(session: Session, min_last_week: int = 0) -> list[int]:
    """The supported seasons a game can be started on, oldest first; min_last_week: only those that long."""
    return [season for season, last in ready_last_weeks(session).items() if last >= min_last_week]


def _two_way(session: Session) -> dict[int, tuple[str, str]]:
    """{player id: (stored position, fantasy position)} for adp_history.TWO_WAY players filed as defenders."""
    rows = session.execute(select(Player.id, Player.position, Player.gsis_id).where(Player.gsis_id.in_(TWO_WAY)))
    return {pid: (stored, TWO_WAY[gsis]) for pid, stored, gsis in rows if stored not in ALL_POSITIONS}


def _skill_games(session: Session, season: int, scoring: str, schedule: Schedule, last_week: int,
                 two_way: Mapping[int, tuple[str, str]]) -> tuple[dict[PlayerWeek, float], dict[PlayerWeek, str],
                                                                  dict[PlayerWeek, Usage], dict[PlayerWeek, BoxLine]]:
    actual, teams, usage, box = {}, {}, {}, {}
    lines = load_game_lines(session, season, scoring_for(scoring))
    if two_way:                                       # the skill-position load leaves them out
        lines.update(load_game_lines(session, season, scoring_for(scoring), list(two_way),
                                     positions=tuple({stored for stored, _ in two_way.values()})))
    for player_id, (_, games) in lines.items():
        for game in games:
            if game.week > last_week:
                continue
            key = (player_id, game.week)
            actual[key] = game.fantasy_points
            usage[key] = Usage(game.snap_pct, game.targets, game.target_share, game.carries)
            box[key] = replay_box.line(game)
            team = schedule.opponent(game.opponent, game.week)      # the opponent's opponent that week
            if team:
                teams[key] = team
    return actual, teams, usage, box


def _kicker_games(session: Session, season: int,
                  last_week: int) -> tuple[dict[PlayerWeek, float], dict[PlayerWeek, str]]:
    actual, teams = {}, {}
    rows = session.scalars(select(KickerGame).join(Player, Player.id == KickerGame.player_id).where(
        KickerGame.season == season, KickerGame.week <= last_week, Player.position == "K"))
    for row in rows:
        key = (row.player_id, row.week)
        actual[key] = float(kdst_model._actual_k(row))
        if row.team:
            teams[key] = row.team
    return actual, teams


def _defense_games(session: Session, season: int, last_week: int, schedule: Schedule,
                   defenses: Mapping[str, int]) -> tuple[dict[PlayerWeek, float], dict[PlayerWeek, str]]:
    """A defense is on its own team every week it plays, whatever stats were loaded."""
    teams = {(defenses[team], week): team for team, week in schedule.opponents
             if team in defenses and week <= last_week}
    actual = {}
    for row in session.scalars(select(TeamGameStats).where(TeamGameStats.season == season,
                                                           TeamGameStats.week <= last_week)):
        # A NULL points_against would score as a shutout, so that game is left out.
        if row.team in defenses and row.points_against is not None:
            actual[(defenses[row.team], row.week)] = float(kdst_model._actual_dst(row))
    return actual, teams


def _projections(session: Session, season: int, scoring: str, last_week: int) -> dict[PlayerWeek, float]:
    """ESPN's number when it has one above zero, otherwise Sleeper's."""
    points = getattr(ReplayProjection, f"pts_{scoring}")
    by_source: dict[str, dict[PlayerWeek, float]] = {source: {} for source in SOURCES}
    rows = session.execute(select(ReplayProjection.player_id, ReplayProjection.week, ReplayProjection.source,
                                  points).where(ReplayProjection.season == season,
                                                ReplayProjection.week.between(1, last_week)))
    for player_id, week, source, value in rows:
        if source in by_source and value and value > 0:
            by_source[source][(player_id, week)] = float(value)
    merged: dict[PlayerWeek, float] = {}
    for source in reversed(SOURCES):
        merged.update(by_source[source])
    return merged


def _pool(session: Session, season: int, ids: set[int], two_way: Mapping[int, tuple[str, str]],
          teams: set[str]) -> dict[int, PoolPlayer]:
    """`teams` are the season's schedule teams: an ADP team code outside them ('FA', an old code) is dropped,
    or that player would be on a bye every week."""
    adp = {row.player_id: row for row in session.scalars(select(ReplayAdp).where(ReplayAdp.season == season))}
    people = session.execute(select(Player.id, Player.full_name, Player.position).where(
        or_(Player.position.in_(ALL_POSITIONS), Player.id.in_(list(two_way)))))
    return {pid: PoolPlayer(pid, name, two_way[pid][1] if pid in two_way else position,
                            adp[pid].adp if pid in adp else None, adp[pid].stdev if pid in adp else None,
                            adp[pid].team if pid in adp and adp[pid].team in teams else None)
            for pid, name, position in people if pid in ids or pid in adp}


def _defense_ids(session: Session) -> dict[str, int]:
    rows = session.execute(select(Player.gsis_id, Player.id).where(Player.position == "DST"))
    return {gsis.removeprefix(DST_PREFIX): pid for gsis, pid in rows if gsis and gsis.startswith(DST_PREFIX)}


def _by_player(teams: Mapping[PlayerWeek, str]) -> dict[int, tuple[tuple[int, str], ...]]:
    grouped: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for (player_id, week), team in teams.items():
        grouped[player_id].append((week, team))
    return {player_id: tuple(sorted(games)) for player_id, games in grouped.items()}


def hard_tag_returns(injuries: Mapping[PlayerWeek, Injury], actual: Mapping[PlayerWeek, float],
                     last_week: int) -> dict[PlayerWeek, int]:
    """{(player, first week of a hard-tag streak): first later week he really played, 0 = never}. A streak
    runs from a hard tag until his next game (byes and untagged weeks in between stay in it). Only the tag
    starts one: whether he played that week anyway is not known before it is played."""
    tagged: dict[int, list[int]] = defaultdict(list)
    for (player_id, week), injury in injuries.items():
        if injury.hard:
            tagged[player_id].append(week)
    out: dict[PlayerWeek, int] = {}
    for player_id, weeks in tagged.items():
        back = None                                     # the week the open streak ends; 0 = it never does
        for week in sorted(weeks):
            if back is None or 0 < back <= week:
                back = next((w for w in range(week + 1, last_week + 1) if (player_id, w) in actual), 0)
                out[(player_id, week)] = back
    return out


def _expert_ranks(session: Session, season: int, schedule: Schedule,
                  players: Mapping[int, PoolPlayer]) -> tuple[dict[PlayerWeek, int], dict[int, int]]:
    from services.replay_experts import week_ranks      # it imports this module

    kickoffs: dict[int, date] = {}
    for game in schedule.games:
        if game.gameday:
            day = date.fromisoformat(game.gameday)
            kickoffs[game.week] = min(kickoffs.get(game.week, day), day)
    rows = [dict(row._mapping) for row in session.execute(select(
        ReplayExpertRank.kind, ReplayExpertRank.scrape_date, ReplayExpertRank.player_id, ReplayExpertRank.position,
        ReplayExpertRank.pos_rank).where(ReplayExpertRank.season == season))]
    ranks, age = week_ranks(rows, kickoffs, {pid: p.position for pid, p in players.items()}) if rows and kickoffs else ({}, {})
    return {key: rank for key, rank in ranks.items() if key[0] in players}, age


def _build(session: Session, season: int, scoring: str) -> SeasonData:
    status = readiness(session, season)
    if not status["ready"]:
        raise NoDataError(f"Replay data for {season} is not ready: {status}")
    loaded = load_schedule(session, season)
    # Final scores are results: replay code uses the schedule for byes and opponents only.
    schedule = replace(loaded, games=tuple(replace(g, home_score=None, away_score=None) for g in loaded.games))
    last_week = max(game.week for game in schedule.games)
    two_way = _two_way(session)
    skill_points, skill_teams, usage, box = _skill_games(session, season, scoring, schedule, last_week, two_way)
    parts = ((skill_points, skill_teams),
             _kicker_games(session, season, last_week),
             _defense_games(session, season, last_week, schedule, _defense_ids(session)))
    actual = {key: points for part, _ in parts for key, points in part.items()}
    teams = {key: team for _, part in parts for key, team in part.items()}
    projected = _projections(session, season, scoring, last_week)
    players = _pool(session, season, {pid for pid, _ in (*actual, *projected)}, two_way,
                    {team for team, _ in schedule.opponents})
    actual, projected, teams, usage, box = ({key: value for key, value in table.items() if key[0] in players}
                                            for table in (actual, projected, teams, usage, box))
    injuries = {(row.player_id, row.week): Injury(row.tag, row.practice, row.injury)
                for row in session.scalars(select(ReplayInjury).where(
                    ReplayInjury.season == season, ReplayInjury.week.between(1, last_week)))
                if row.player_id in players}
    expert_ranks, expert_age = _expert_ranks(session, season, schedule, players)
    unplaced = sum(1 for key in actual if key not in teams)
    if unplaced > len(actual) * 0.05:
        log.warning("Replay %s: %d of %d games have no team (schedule and stats team codes may differ)",
                    season, unplaced, len(actual))
    return SeasonData(season=season, scoring=scoring, players=MappingProxyType(players), schedule=schedule,
                      actual=MappingProxyType(actual), projected=MappingProxyType(projected),
                      team_by_week=MappingProxyType(_by_player(teams)), last_week=last_week,
                      injuries=MappingProxyType(injuries), usage=MappingProxyType(usage),
                      expert_ranks=MappingProxyType(expert_ranks), expert_age=MappingProxyType(expert_age),
                      returns=MappingProxyType(hard_tag_returns(injuries, actual, last_week)),
                      box=MappingProxyType(box),
                      defense_prior=MappingProxyType(replay_defense.load_prior(session, season, scoring)))
