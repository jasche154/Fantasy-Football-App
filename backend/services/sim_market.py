"""Simulated seasons: what the public sees before each week (projections, expert ranks, ADP, buzz).

Everything here reads a BELIEF World (sim_world.estimate), never the truth, except `buzz`, which turns the
truth's dated role changes into a noisy hint.

- `projections`: each active player's mean points in his game that week (sim_game.expected) under the
  belief, with the week's hard-tagged players out, so their teammates' projections rise. Kept above
  MIN_PROJECTION, like the real tables (a player out or on bye has none).
- `values`: mean points a game over the games left against a league-average opponent: a player believed out
  (`out_weeks`) scores nothing in his first out_weeks games and his active teammates share his work meanwhile.
- `expert_ranks`: position ranks by value x lognormal EXPERT_NOISE among the players known that week.
- `adp`: a draft under the belief on the same values (so a draft room's expert order and ADP agree), in the
  league's scoring. A preseason market also leans on what it saw: with a `record` (`track_record`: raw points
  a game last season) each value is (1 - w) x value + w x record, w = the tuned `market.record_weight`. Skill
  players go by points over a replacement rank per position (the tuned `market.replacement`; for a superflex
  / 2-QB list the QB's is `market.two_qb_replacement`, set so QB1-QB12 go about where the real 2026 2-QB list
  takes them). The blend weight and replacement ranks maximize agreement with the real ADP lists 2022-2025
  (Spearman over the real top 150; each held-out season agrees as well as the fitted ones). Kickers and
  defenses at the real average ADP of the Nth one taken (`kdst_adp` in sim_params.json), stdev = a + b x adp
  as measured. A player believed out for the games left gets none.
- `buzz`: before the week a change starts, HINT_RATE of true breakouts, busts and benchings get a news line;
  false lines (random leads, backups and starting QBs) are added so that about PRECISION of all lines are true
  (sim_season passes no false rate outside the weeks a change can start, so those weeks stay quiet).
"""
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping

import numpy as np

from services.replay_pool import SeasonData
from services.schedule_strength import Schedule
from services.scoring import ScoringSettings, scoring_for
from services.sim_game import expected
from services.sim_world import RoleChange, World, params

MIN_PROJECTION = 0.1
EXPERT_NOISE = 0.08              # expert opinion vs the belief (lognormal sigma)
EXPERT_AGE = {"pre": 6, "ros": 2}     # days before the Sunday, as the real snapshots are (week 0 / later weeks)
SEASON_GAMES = 17
# The replacement rank at each position: defaults, overlaid by the tuned `market.replacement`.
REPLACEMENT = {"QB": 26, "RB": 32, "WR": 38, "TE": 14}
ADP_SKILL = 270                  # skill players on a draft board
MIN_ADP_STDEV = 0.5
RECORD_MIN_GAMES = 4             # a track record needs this many games
HINT_RATE = 0.6
PRECISION = 0.6
BUZZ = {"breakout": "Beat reporters say his role is growing; expect more work.",
        "bust": "Reports say his snaps and touches are shrinking.",
        "bench": "Reports say his starting job is in danger."}


def projections(belief: World, schedule: Schedule, week: int, out: Collection[int],
                scoring: ScoringSettings) -> dict[int, float]:
    """{player: projected points} for every game of `week` (see the module docstring)."""
    found = {}
    for game in schedule.games:
        if game.week != week or game.home_team not in belief.teams or game.away_team not in belief.teams:
            continue
        for pid, line in expected(belief, game.home_team, game.away_team, out, scoring).items():
            if line.points >= MIN_PROJECTION:
                found[pid] = round(line.points, 2)
    return found


def values(belief: World, scoring: ScoringSettings, games_left: int = SEASON_GAMES) -> dict[int, float]:
    """{player: mean points a game over the next `games_left` games}: a player believed out (`out_weeks`) scores
    nothing in his first out_weeks games, and his teammates share his work meanwhile."""
    phases = sorted({0, games_left} | {min(p.out_weeks, games_left) for p in belief.players.values()})
    found: dict[int, float] = defaultdict(float)
    for team in sorted(belief.teams):
        roster, lines = belief.roster(team), {}          # lines: one expected() per distinct set of sitters
        for start, end in zip(phases, phases[1:]):
            sitting = frozenset(p.player_id for p in roster if p.out_weeks > start)
            if sitting not in lines:
                lines[sitting] = expected(belief, team, None, sitting, scoring)
            for pid, line in lines[sitting].items():
                found[pid] += line.points * (end - start) / max(1, games_left)
    return dict(found)


def expert_ranks(belief: World, known: Iterable[int], week: int, dice: np.random.Generator, scoring: ScoringSettings,
                 games_left: int) -> dict[tuple[int, int], int]:
    """{(player, week): position rank} among the `known` players the belief places on a team."""
    worth = values(belief, scoring, games_left)
    ids = sorted(pid for pid in set(known) if pid in belief.players)
    noise = dice.lognormal(0.0, EXPERT_NOISE, len(ids))
    opinion = {pid: worth.get(pid, 0.0) * n for pid, n in zip(ids, noise)}
    ranks = {}
    for position in ("QB", "RB", "WR", "TE", "K", "DST"):
        order = sorted((pid for pid in ids if belief.players[pid].position == position), key=lambda pid: (-opinion[pid], pid))
        ranks.update({(pid, week): rank for rank, pid in enumerate(order, start=1)})
    return ranks


def adp(world: World, scoring: ScoringSettings | None = None, two_qb: bool = False,
        games_left: int = SEASON_GAMES, record: Mapping[int, float] | None = None) -> dict[int, tuple[float, float]]:
    """{player: (adp, stdev)} for a draft held under `world` with `games_left` games to play (see the module
    docstring): the league's `scoring` (default PPR), a superflex / 2-QB league's list when `two_qb`, each
    value blended with his `record` (points a game last season, `track_record`) when given."""
    market = params()["market"]
    season = values(world, scoring or scoring_for("ppr"), games_left)
    weight = market["record_weight"] if record else 0.0
    season = {pid: (1 - weight) * v + weight * record.get(pid, v) if record else v for pid, v in season.items()}
    healthy = {pid: v * SEASON_GAMES for pid, v in season.items() if world.players[pid].out_weeks < games_left}
    intercept, slope = params()["adp_stdev"]
    board: dict[int, float] = {}
    for position, picks in params()["kdst_adp"].items():
        ranked = sorted((pid for pid in healthy if world.players[pid].position == position), key=lambda p: (-healthy[p], p))
        board.update({pid: picks[i] for i, pid in enumerate(ranked[:len(picks)])})
    baseline = {}
    replacement = {**REPLACEMENT, **market.get("replacement", {})}
    replacement = {**replacement, "QB": market["two_qb_replacement"]} if two_qb else replacement
    for position, rank in replacement.items():
        points = sorted((v for pid, v in healthy.items() if world.players[pid].position == position), reverse=True)
        baseline[position] = points[min(rank, len(points)) - 1] if points else 0.0
    skill = sorted((pid for pid in healthy if world.players[pid].position in REPLACEMENT),
                   key=lambda p: (-(healthy[p] - baseline[world.players[p].position]), p))[:ADP_SKILL]
    taken = {round(v) for v in board.values()}
    slot = 0
    for pid in skill:
        slot += 1
        while slot in taken:
            slot += 1
        board[pid] = float(slot)
    return {pid: (round(value, 1), round(max(MIN_ADP_STDEV, intercept + slope * value), 1)) for pid, value in board.items()}


def track_record(data: SeasonData) -> dict[int, float]:
    """{player: points a game over his games in `data`} for players with RECORD_MIN_GAMES or more (the market's
    memory of a season: raw production, not regressed)."""
    totals: dict[int, list[float]] = defaultdict(list)
    for (pid, _), points in data.actual.items():
        totals[pid].append(points)
    return {pid: sum(games) / len(games) for pid, games in totals.items() if len(games) >= RECORD_MIN_GAMES}


def buzz(changes: Iterable[RoleChange], belief: World, week: int, dice: np.random.Generator,
         false_rate: float, out: Collection[int] = ()) -> dict[tuple[int, int], str]:
    """{(player, week): news line} for `week` (see the module docstring). false_rate: the expected true lines
    per week, from which the false ones are sized. Never about a player who is out that week (`out`: the
    week's hard tags, or believed out): a role rumour about a player on IR makes no sense."""
    sidelined = set(out) | {pid for pid, p in belief.players.items() if p.out_weeks > 0}
    notes = {}
    for change in sorted(changes, key=lambda c: c.player_id):
        hinted = change.week == week and change.kind in BUZZ and dice.random() < HINT_RATE
        if hinted and change.player_id not in sidelined:
            notes[(change.player_id, week)] = BUZZ[change.kind]
    candidates = sorted(pid for pid, p in belief.players.items() if pid not in sidelined and (
        (p.position == "QB" and p.starts >= 0.5) or (p.position in ("RB", "WR", "TE") and p.targets + p.carries >= 0.08)))
    count = int(dice.poisson(false_rate * (1 - PRECISION) / PRECISION))
    for pid in dice.choice(candidates, size=min(count, len(candidates)), replace=False) if candidates else ():
        pid = int(pid)
        kind = "bench" if belief.players[pid].position == "QB" else ("breakout", "bust")[int(dice.integers(2))]
        notes.setdefault((pid, week), BUZZ[kind])
    return notes


def expected_hints(teams: int, weeks: int, knobs: Mapping) -> float:
    """True buzz lines per week the talent model makes on average (sizes the false ones)."""
    per_team = knobs["breakout_rate"] + knobs["bust_rate"] + knobs["bench_rate"]
    return teams * per_team * HINT_RATE / max(1, weeks)
