"""Simulated seasons: what the public sees before each week (projections, expert ranks, ADP, buzz).

Everything here reads a BELIEF World (sim_world.estimate), never the truth, except `buzz`, which turns the
truth's dated role changes into a noisy hint.

- `projections`: each active player's mean points in his game that week (sim_game.expected) under the
  belief, with the week's hard-tagged players out, so their teammates' projections rise. Kept above
  MIN_PROJECTION, like the real tables (a player out or on bye has none).
- `values`: points per game against a league-average opponent with every healthy teammate active, times the
  share of the remaining games he is believed to play (`out_weeks`).
- `expert_ranks`: position ranks by value x lognormal EXPERT_NOISE among the players known that week.
- `adp`: a draft under the belief (PPR, as the stored ADP lists are): skill players by points over a
  12-team replacement level (REPLACEMENT) over SEASON_GAMES, kickers and defenses at the real average ADP of
  the Nth one taken (`kdst_adp` in sim_params.json), stdev = a + b x adp as measured. A player believed out
  for the season gets none.
- `buzz`: before the week a change starts, HINT_RATE of true breakouts, busts and benchings get a news line;
  false lines (random leads, backups and starting QBs) are added so that about PRECISION of all lines are true.
"""
from collections.abc import Collection, Iterable, Mapping

import numpy as np

from services.schedule_strength import Schedule
from services.scoring import ScoringSettings, scoring_for
from services.sim_game import expected
from services.sim_world import RoleChange, World, params

MIN_PROJECTION = 0.1
EXPERT_NOISE = 0.08              # expert opinion vs the belief (lognormal sigma)
EXPERT_AGE = {"pre": 6, "ros": 2}     # days before the Sunday, as the real snapshots are (week 0 / later weeks)
SEASON_GAMES = 17
# The replacement rank at each position (12 teams). QB's is set against real ADP, not the QB14 a 12-team lineup
# implies: drafters take the top QBs earlier than that (2022-2025: top-5 QBs at about pick 34; QB14 put them at 50).
REPLACEMENT = {"QB": 26, "RB": 32, "WR": 38, "TE": 14}
ADP_SKILL = 270                  # skill players on a draft board
MIN_ADP_STDEV = 0.5
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


def values(belief: World, scoring: ScoringSettings, out: Collection[int] = (), games_left: int = SEASON_GAMES) -> dict[int, float]:
    """{player: points per game x the share of games_left he is believed to play}."""
    found = {}
    for team in sorted(belief.teams):
        for pid, line in expected(belief, team, None, out, scoring).items():
            missed = min(belief.players[pid].out_weeks, games_left) if pid in belief.players else 0
            found[pid] = line.points * (games_left - missed) / max(1, games_left)
    return found


def expert_ranks(belief: World, known: Iterable[int], week: int, dice: np.random.Generator, scoring: ScoringSettings,
                 out: Collection[int], games_left: int) -> dict[tuple[int, int], int]:
    """{(player, week): position rank} among the `known` players the belief places on a team."""
    worth = values(belief, scoring, out, games_left)
    ids = sorted(pid for pid in set(known) if pid in belief.players)
    noise = dice.lognormal(0.0, EXPERT_NOISE, len(ids))
    opinion = {pid: worth.get(pid, 0.0) * n for pid, n in zip(ids, noise)}
    ranks = {}
    for position in ("QB", "RB", "WR", "TE", "K", "DST"):
        order = sorted((pid for pid in ids if belief.players[pid].position == position), key=lambda pid: (-opinion[pid], pid))
        ranks.update({(pid, week): rank for rank, pid in enumerate(order, start=1)})
    return ranks


def adp(world: World) -> dict[int, tuple[float, float]]:
    """{player: (adp, stdev)} for a draft held under `world` (see the module docstring)."""
    season = values(world, scoring_for("ppr"))
    healthy = {pid: v * SEASON_GAMES for pid, v in season.items() if world.players[pid].out_weeks < SEASON_GAMES}
    intercept, slope = params()["adp_stdev"]
    board: dict[int, float] = {}
    for position, picks in params()["kdst_adp"].items():
        ranked = sorted((pid for pid in healthy if world.players[pid].position == position), key=lambda p: (-healthy[p], p))
        board.update({pid: picks[i] for i, pid in enumerate(ranked[:len(picks)])})
    baseline = {}
    for position, rank in REPLACEMENT.items():
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


def buzz(changes: Iterable[RoleChange], belief: World, week: int, dice: np.random.Generator,
         false_rate: float) -> dict[tuple[int, int], str]:
    """{(player, week): news line} for `week` (see the module docstring). false_rate: the expected true lines
    per week, from which the false ones are sized."""
    notes = {}
    for change in sorted(changes, key=lambda c: c.player_id):
        if change.week == week and change.kind in BUZZ and dice.random() < HINT_RATE:
            notes[(change.player_id, week)] = BUZZ[change.kind]
    candidates = sorted(pid for pid, p in belief.players.items()
                        if (p.position == "QB" and p.starts >= 0.5) or (p.position in ("RB", "WR", "TE") and p.targets + p.carries >= 0.08))
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
