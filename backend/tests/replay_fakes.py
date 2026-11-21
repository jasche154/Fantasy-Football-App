"""A synthetic NFL season for Replay League tests: deterministic, fast, no network and no real database.

`fake_season()` returns a `SeasonData` with 32 real team codes, byes in weeks 5-12 and 366 players whose
scoring looks like half-PPR fantasy (QB1 about 22 a game, RB1 20, WR1 19, TE1 14, K 8, DST 7). Each player
has a preseason reputation (`perceived`, which sets his ADP and early projections) and a true level
(`talent`, which sets his real points); projections drift from the first to the second over six weeks.

Player ids are fixed by position and preseason rank (`player_id("RB", 3)`), so the scripted stories below
are the same players for every seed. `drafted_state` is a league just after its draft (no replay_draft
import). `seed_db` writes a season into the real tables in a form `replay_pool.load_season_data` reads
back to the same data.
"""
import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from functools import lru_cache
from types import MappingProxyType

import numpy as np
from sqlalchemy import insert
from sqlalchemy.orm import Session

from database.models import (
    KickerGame, NflGame, Player, PlayerStatsWeekly, ReplayAdp, ReplayProjection, TeamGameStats,
)
from services import replay_values
from services.kdst_rankings import DST_PREFIX, TEAM_NAMES
from services.replay_pool import ALL_POSITIONS, SKILL, PoolPlayer, SeasonData
from services.replay_state import Pick, Settings, State, Team, round_robin
from services.schedule_strength import Game, Schedule
from services.scoring import POINTS_PER_RECEPTION

SEASON = 2021
TEAMS = tuple(sorted(TEAM_NAMES))
POOL = {"QB": 40, "RB": 90, "WR": 120, "TE": 50, "K": 34, "DST": 32}
ID_BASE = {"QB": 10_000, "RB": 11_000, "WR": 12_000, "TE": 13_000, "K": 14_000, "DST": 15_000}
BYE_WEEKS = range(5, 13)
ADP_SKILL = 196                         # skill players with an ADP; with 12 K and 12 DST that is 220
ADP_KDST = {"DST": 150.0, "K": 160.0}   # where the first one goes; one more every 4 picks
ADP_KDST_COUNT = 12
VALUE_BASELINE = {"QB": 12, "RB": 30, "WR": 36, "TE": 12}     # the preseason rank worth nothing in a draft
LEARN_WEEKS = 5                         # projections reach a player's true level by week 6
MISS_RATE = 0.03                        # chance a skill player sits out any one week after week 1
TALENT_NOISE = {"QB": 0.08, "RB": 0.15, "WR": 0.15, "TE": 0.15, "K": 0.06, "DST": 0.08}   # talent vs reputation


def player_id(position: str, rank: int) -> int:
    """The id of the player with that preseason rank at his position (1 = best)."""
    return ID_BASE[position] + rank


BREAKOUT_ID = player_id("RB", 51)       # late ADP, plays like a top-five back all year
BUST_ID = player_id("WR", 4)            # first-round ADP, plays like a WR5
INJURED_STAR_ID = player_id("RB", 3)    # out (no projection, no game) in INJURED_WEEKS; bye in week 12
INJURED_WEEKS = (6, 7, 8, 9)
TRADED_ID = player_id("WR", 21)         # changes NFL team: his first game for the new one is TRADED_WEEK
TRADED_WEEK = 9
NEVER_PLAYED_ID = player_id("WR", 30)   # drafted mid-round on his ADP; no game and no projection all season;
                                        # his team is the one stored with his ADP (PoolPlayer.adp_team)
EVERY_WEEK = tuple(range(1, 19))
STORY_IDS = frozenset({BREAKOUT_ID, BUST_ID, INJURED_STAR_ID, TRADED_ID, NEVER_PLAYED_ID})
KICKER_CHANGES = ((31, 33, 9), (32, 34, 11))     # (starter rank, replacement rank, replacement's first week)
SCORING_SHIFT = {"ppr": 2.0, "half": 1.0, "std": 0.0}        # seed_db: skill projections differ by format
SLEEPER_DECOY = 1.5                     # seed_db: Sleeper's number where ESPN also has one


@dataclass(frozen=True)
class _Spec:
    player_id: int
    name: str
    position: str
    perceived: float                    # preseason points per game in the market's eyes
    talent: float                       # true points per game
    team: str
    out_weeks: tuple[int, ...] = ()
    move: tuple[int, str] | None = None     # (first week on the new team, new team)

    def team_in(self, week: int) -> str:
        return self.move[1] if self.move and week >= self.move[0] else self.team


def _level(position: str, index: int) -> float:
    """Points per game of the player ranked `index` (0 = best) at a position."""
    if position == "QB":
        return 22.5 - 0.33 * index if index < 32 else 2.0        # 32 starters, then backups
    if position == "RB":
        return 1.5 + 19.0 * math.exp(-index / 30)
    if position == "WR":
        return 1.5 + 18.0 * math.exp(-index / 38)
    if position == "TE":
        return 1.5 + 13.0 * math.exp(-index / 14)
    return 9.5 - 0.1 * index if position == "K" else 9.0 - 0.13 * index


def _pairings(teams: list[str], round_: int) -> list[tuple[str, str]]:
    """One round of a round-robin (circle method); home and away alternate."""
    rest = teams[1:]
    turn = round_ % len(rest)
    circle = [teams[0], *rest[turn:], *rest[:turn]]
    pairs = [(circle[i], circle[-1 - i]) for i in range(len(circle) // 2)]
    return [pair if (i + round_) % 2 else pair[::-1] for i, pair in enumerate(pairs)]


def _schedule(order: list[str], weeks: int) -> Schedule:
    """Four teams rest in each of BYE_WEEKS: order[i] is off in week 5 + i % 8."""
    games, opponents = [], {}
    for week in range(1, weeks + 1):
        playing = [team for i, team in enumerate(order) if BYE_WEEKS[i % len(BYE_WEEKS)] != week]
        for home, away in _pairings(playing, week):
            games.append(Game(week=week, home_team=home, away_team=away, home_score=None, away_score=None,
                              spread_line=None, total_line=None))
            opponents[(home, week)] = away
            opponents[(away, week)] = home
    return Schedule(games=tuple(games), opponents=MappingProxyType(opponents))


def _scripted(spec: _Spec, order: list[str]) -> _Spec:
    if spec.player_id == BREAKOUT_ID:
        return replace(spec, name="Breakout Back", talent=17.0)
    if spec.player_id == BUST_ID:
        return replace(spec, name="Early Bust", talent=6.5)
    if spec.player_id == INJURED_STAR_ID:
        return replace(spec, name="Injured Star", talent=spec.perceived, team=order[7], out_weeks=INJURED_WEEKS)
    if spec.player_id == TRADED_ID:      # both teams rest in week 5, so the move never meets a bye
        return replace(spec, name="Traded Wideout", talent=spec.perceived, team=order[0],
                       move=(TRADED_WEEK, order[8]))
    if spec.player_id == NEVER_PLAYED_ID:
        return replace(spec, name="Never Played", out_weeks=EVERY_WEEK)
    return spec


def _kicker_changes(specs: list[_Spec], weeks: int) -> list[_Spec]:
    """Two teams change kickers mid-season: the replacement has no ADP and nothing before his first week."""
    by_id = {spec.player_id: spec for spec in specs}
    for starter, backup, first_week in KICKER_CHANGES:
        old = by_id[player_id("K", starter)]
        by_id[old.player_id] = replace(old, out_weeks=tuple(range(first_week, weeks + 1)))
        by_id[player_id("K", backup)] = replace(by_id[player_id("K", backup)], team=old.team,
                                                out_weeks=tuple(range(1, first_week)))
    return list(by_id.values())


def _specs(rng: np.random.Generator, order: list[str], weeks: int) -> list[_Spec]:
    specs = []
    for position, size in POOL.items():
        seats = rng.permutation(len(order))
        noise = rng.lognormal(0.0, TALENT_NOISE[position], size)
        for index in range(size):
            level, team = _level(position, index), order[seats[index % len(order)]]
            name = f"{TEAM_NAMES[team]} D/ST" if position == "DST" else f"Replay {position}{index + 1}"
            specs.append(_scripted(_Spec(player_id(position, index + 1), name, position, round(level, 2),
                                         round(level * noise[index], 2), team), order))
    return _kicker_changes(specs, weeks)


def _adp(specs: list[_Spec], rng: np.random.Generator) -> dict[int, tuple[float, float | None]]:
    """{player_id: (adp, stdev)}: skill players by preseason value over a baseline, then K and DST late."""
    skill = [spec for spec in specs if spec.position in SKILL]
    worth = {spec.player_id: spec.perceived * opinion - _level(spec.position, VALUE_BASELINE[spec.position])
             for spec, opinion in zip(skill, rng.lognormal(0.0, 0.06, len(skill)))}
    board = sorted(skill, key=lambda spec: -worth[spec.player_id])[:ADP_SKILL]
    jitter = rng.uniform(-0.4, 0.4, len(board))
    adp: dict[int, tuple[float, float | None]] = {}
    for pick, spec in enumerate(board, start=1):
        value = round(max(1.0, pick + jitter[pick - 1]), 1)
        adp[spec.player_id] = (value, round(1.5 + 0.1 * value, 1))
    for position, first in ADP_KDST.items():
        for rank, late in enumerate(rng.uniform(0.0, 2.0, ADP_KDST_COUNT), start=1):
            adp[player_id(position, rank)] = (round(first + 4 * (rank - 1) + late, 1), None)
    return adp


def _games(spec: _Spec, schedule: Schedule, weeks: int, missed: np.ndarray) -> tuple[tuple[int, str], ...]:
    """((week, team) for every game he plays): not on a bye, not scripted out, not a random week off."""
    can_miss = spec.position in SKILL and spec.player_id not in STORY_IDS
    return tuple((week, spec.team_in(week)) for week in range(1, weeks + 1)
                 if week not in spec.out_weeks and (spec.team_in(week), week) in schedule.opponents
                 and not (can_miss and week > 1 and missed[week - 1]))


def _projection(spec: _Spec, week: int, noise: float) -> float:
    belief = spec.perceived + (spec.talent - spec.perceived) * min(1.0, (week - 1) / LEARN_WEEKS)
    return float(round(max(0.1, belief * noise), 1))      # plain floats, as the database gives


def _points(spec: _Spec, luck: float, bounce: float) -> float:
    if spec.position in SKILL:
        return float(round(spec.talent * luck, 1))
    if spec.position == "K":                                  # whole numbers, as kickers and defenses score
        return float(max(0, round(spec.talent * (1 + 0.45 * bounce))))
    return float(max(-4, round(spec.talent + 5.5 * bounce)))


@lru_cache(maxsize=8)
def _season(season: int, scoring: str, seed: int, weeks: int) -> SeasonData:
    if scoring not in POINTS_PER_RECEPTION or weeks < 1:
        raise ValueError(f"Can't fake a '{scoring}' season of {weeks} weeks.")
    rng = np.random.default_rng([season, seed])
    order = [str(team) for team in rng.permutation(TEAMS)]
    schedule = _schedule(order, weeks)
    specs = _specs(rng, order, weeks)
    adp = _adp(specs, rng)
    shape = (len(specs), weeks)
    noise, luck = rng.lognormal(0.0, 0.10, shape), rng.gamma(4.0, 0.25, shape)
    bounce, missed = rng.standard_normal(shape), rng.random(shape) < MISS_RATE
    players, actual, projected, team_by_week = {}, {}, {}, {}
    for row, spec in enumerate(specs):
        games = _games(spec, schedule, weeks, missed[row])
        if not games and spec.player_id not in adp:
            continue                                          # never seen: not in the pool
        players[spec.player_id] = PoolPlayer(spec.player_id, spec.name, spec.position, *adp.get(spec.player_id, ()),
                                             adp_team=spec.team if spec.player_id == NEVER_PLAYED_ID else None)
        if games:
            team_by_week[spec.player_id] = games
        for week, _ in games:
            projected[(spec.player_id, week)] = _projection(spec, week, noise[row, week - 1])
            actual[(spec.player_id, week)] = _points(spec, luck[row, week - 1], bounce[row, week - 1])
    return SeasonData(season=season, scoring=scoring, players=MappingProxyType(players), schedule=schedule,
                      actual=MappingProxyType(actual), projected=MappingProxyType(projected),
                      team_by_week=MappingProxyType(team_by_week), last_week=weeks)


def fake_season(season: int = SEASON, scoring: str = "half", seed: int = 0, weeks: int = 17) -> SeasonData:
    """The synthetic season (the same object for the same arguments, so value caches hit across tests)."""
    return _season(season, scoring, seed, weeks)


def fake_settings(**overrides) -> Settings:
    return Settings(**{"season": SEASON, **overrides})


def fake_knowledge(week: int, settings: Settings | None = None,
                   data: SeasonData | None = None) -> replay_values.Knowledge:
    settings = settings or fake_settings()
    return replay_values.knowledge(data or fake_season(settings.season, settings.scoring),
                                   replay_values.league_settings(settings), week, settings.end_week)


@lru_cache(maxsize=32)
def _drafted(settings: Settings, data: SeasonData, seed: int) -> State:
    board = sorted(data.players.values(),                       # straight ADP, then by week-1 projection
                   key=lambda p: (p.adp is None, p.adp or 0.0, -data.projection(p.player_id, 1), p.player_id))
    ids = tuple(range(1, settings.teams + 1))
    rosters: dict[int, list[PoolPlayer]] = {team_id: [] for team_id in ids}
    picks = []
    for overall in range(1, settings.teams * settings.rounds + 1):
        round_, spot = divmod(overall - 1, settings.teams)
        team_id = settings.teams - spot if round_ % 2 else spot + 1          # snake
        mine = [player.position for player in rosters[team_id]]
        left = settings.rounds - len(mine) - 1                               # picks after this one
        choice = next(p for p in board if replay_values.lineup_holes([*mine, p.position], settings) <= left)
        board.remove(choice)
        rosters[team_id].append(choice)
        picks.append(Pick(overall, round_ + 1, team_id, choice.player_id))
    teams = tuple(Team(i, "You" if i == settings.draft_slot else f"CPU {i}", i == settings.draft_slot,
                       tuple(player.player_id for player in rosters[i]), 1.0, dict.fromkeys(ALL_POSITIONS, 1.0))
                  for i in ids)
    return State(phase="open", week=1, teams=teams, draft_order=ids, picks=tuple(picks),
                 schedule=round_robin(ids, settings.regular_season_weeks, seed), results=(),
                 waiver_order=ids[::-1], claims=(), locked=(), lineup=None, offers=(), events=(), traded={})


def drafted_state(settings: Settings | None = None, data: SeasonData | None = None, seed: int = 0) -> State:
    """A league just after its draft: a straight-ADP snake (each team takes the best ADP left that still lets
    it fill every starting slot, K and DST included), team ids 1..N with the user at settings.draft_slot,
    schedule built, phase "open", week 1. Cached per arguments; NEVER_PLAYED_ID is on a roster."""
    settings = settings or fake_settings()
    return _drafted(settings, data or fake_season(settings.season, settings.scoring), seed)


def _overlaid(table: Mapping, changes: Mapping | None) -> Mapping:
    merged = {**table, **(changes or {})}
    return MappingProxyType({key: value for key, value in merged.items() if value is not None})


def patched(data: SeasonData, projected: Mapping[tuple[int, int], float | None] | None = None,
            actual: Mapping[tuple[int, int], float | None] | None = None) -> SeasonData:
    """A copy with some (player_id, week) numbers replaced; None removes the entry. Teams are unchanged."""
    return replace(data, projected=_overlaid(data.projected, projected), actual=_overlaid(data.actual, actual))


def future_changed(data: SeasonData, after_week: int, seed: int = 1) -> SeasonData:
    """A copy whose real points and projections for every week after `after_week` are different
    (about one projection in ten disappears), and identical up to it: the no-hindsight probe."""
    rng = np.random.default_rng(seed)

    def shaken(table: Mapping[tuple[int, int], float], drop: float) -> dict[tuple[int, int], float | None]:
        return {key: None if rng.random() < drop else round(abs(value) * rng.uniform(1.0, 2.5) + 1.0, 1)
                for key, value in table.items() if key[1] > after_week}      # always above the old number

    return patched(data, projected=shaken(data.projected, 0.1), actual=shaken(data.actual, 0.0))


# ---------------- writing a season into the real tables ----------------

def _player_rows(data: SeasonData) -> list[dict]:
    rows = []
    for pid, player in data.players.items():
        games = data.team_by_week.get(pid, ())
        team = games[-1][1] if games else None
        gsis = f"{DST_PREFIX}{team}" if player.position == "DST" else f"00-RPL{pid}"
        rows.append({"id": pid, "gsis_id": gsis, "full_name": player.name, "position": player.position,
                     "nfl_team": team})
    return rows


def _game_rows(data: SeasonData) -> list[dict]:
    return [{"game_id": f"{data.season}_{g.week:02d}_{g.away_team}_{g.home_team}", "season": data.season,
             "week": g.week, "game_type": "REG", "home_team": g.home_team, "away_team": g.away_team}
            for g in data.schedule.games]


def _skill_row(data: SeasonData, pid: int, week: int, points: float, opponent: str | None) -> dict:
    """A stat line worth exactly `points` in data.scoring (and one point more per step toward full PPR)."""
    receptions = 2 if data.players[pid].position != "QB" and points >= 3 else 0
    yards = round((points - receptions * POINTS_PER_RECEPTION[data.scoring]) * 10)
    return {"player_id": pid, "season": data.season, "week": week, "season_type": "REG",
            "opponent_team": opponent, "receptions": receptions, "rushing_yards": yards}


def _kicker_row(data: SeasonData, pid: int, week: int, points: float, team: str | None,
                opponent: str | None) -> dict:
    threes, ones = divmod(int(points), 3)
    return {"player_id": pid, "season": data.season, "week": week, "team": team, "opponent": opponent,
            "made_0_39": threes, "made_40_49": 0, "made_50": 0, "missed_0_39": 0, "missed_40_49": 0,
            "missed_50": 0, "pat_made": ones, "pat_att": ones}


def _defense_row(data: SeasonData, week: int, points: float, team: str, opponent: str | None) -> dict:
    """Sacks plus a points-allowed tier that add up to `points` (whole, -4 or more)."""
    points = int(points)
    sacks, allowed = ((points - 1, 17) if points >= 1 else (0, 24) if points == 0
                      else (0, 30) if points == -1 else (points + 4, 40))
    quiet = dict.fromkeys(("plays", "dropbacks", "pass_epa", "rush_epa", "sacks_taken", "ints_thrown",
                           "fumbles_lost", "def_ints", "def_fumble_rec", "def_tds", "def_safeties", "def_blocks",
                           "rz_trips", "rz_tds", "fourth_downs", "fourth_down_goes", "fg_att", "fg_made",
                           "pat_att", "pat_made"), 0)
    return {**quiet, "season": data.season, "week": week, "team": team, "opponent": opponent or "",
            "points_for": 20, "points_against": allowed, "def_sacks": sacks}


def _result_rows(data: SeasonData) -> dict[type, list[dict]]:
    teams = {(pid, week): team for pid, games in data.team_by_week.items() for week, team in games}
    rows: dict[type, list[dict]] = {PlayerStatsWeekly: [], KickerGame: [], TeamGameStats: []}
    for (pid, week), points in data.actual.items():
        team, position = teams.get((pid, week)), data.players[pid].position
        opponent = data.schedule.opponent(team, week)
        if position in SKILL:
            rows[PlayerStatsWeekly].append(_skill_row(data, pid, week, points, opponent))
        elif position == "K":
            rows[KickerGame].append(_kicker_row(data, pid, week, points, team, opponent))
        else:
            rows[TeamGameStats].append(_defense_row(data, week, points, team, opponent))
    return rows


def _projection_rows(data: SeasonData) -> list[dict]:
    """ESPN has the real number, Sleeper a decoy; in one week in seven ESPN has nothing (or a zero) and
    Sleeper carries the real number. Skill rows differ by scoring format, K and DST rows don't."""
    rows = []
    for (pid, week), value in data.projected.items():
        flat = data.players[pid].position not in SKILL
        gap = (pid + week) % 7 == 0
        espn = value if not gap else 0.0 if (pid + week) % 14 == 0 else None
        for source, points in (("espn", espn), ("sleeper", value if gap else value + SLEEPER_DECOY)):
            if points is None:
                continue
            shifts = {name: 0.0 if flat or points == 0 else shift - SCORING_SHIFT[data.scoring]
                      for name, shift in SCORING_SHIFT.items()}
            rows.append({"season": data.season, "week": week, "player_id": pid, "source": source,
                         **{f"pts_{name}": round(points + shift, 2) for name, shift in shifts.items()}})
    return rows


def seed_db(session: Session, data: SeasonData) -> None:
    """Write the season into the real tables (players keep their fake ids) and commit.

    Afterwards `replay_pool.load_season_data(session, data.season, data.scoring)` returns the same pool,
    points, projections, teams and schedule. K and DST points must be whole numbers (DST -4 or more)."""
    tables = {Player: _player_rows(data), NflGame: _game_rows(data), **_result_rows(data),
              ReplayAdp: [{"season": data.season, "player_id": pid, "adp": p.adp, "stdev": p.adp_stdev,
                           "source": "ffc", "team": p.adp_team}
                          for pid, p in data.players.items() if p.adp is not None],
              ReplayProjection: _projection_rows(data)}
    for model, rows in tables.items():
        if rows:
            session.execute(insert(model), rows)
    session.commit()
