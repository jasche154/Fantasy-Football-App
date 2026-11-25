"""Simulated seasons: what the public sees before each week (projections, expert ranks, ADP, buzz).

Everything here reads a BELIEF World (sim_world.estimate), never the truth, except `buzz`, which turns the
truth's dated role changes into a noisy hint.

- `projections`: each active player's mean points in his game that week (sim_game.expected) under the
  belief, with the week's hard-tagged players out, so their teammates' projections rise, centred on the game's
  pregame line (sim_lines) as the game itself is. Kept above
  MIN_PROJECTION, like the real tables (a player out or on bye has none).
- `values`: mean points a game over the games left against a league-average opponent: a player believed out
  (`out_weeks`) scores nothing in his first out_weeks games and his active teammates share his work meanwhile.
- `expert_ranks`: position ranks by value (in a simulated week, with each team's scoring edge from the line
  model, sim_lines.edges: experts know which offenses score, as the week's projections do) x a lognormal opinion among the players known that week: sigma
  EXPERT_NOISE x the position's OPINION_SPREAD, so the noise moves ranks about as much at every position (QB
  values are bunched: the same 8% that barely moves a running back reshuffles the QBs). A simulated season
  carries each player's opinion from week to week (`opinion`: an AR(1), each week keeping OPINION_KEEP of the
  last and a fresh draw for the rest), and the value they rank a healthy player on moves only WORTH_STEP of
  the way to this week's (`held`: experts revise slowly; a player believed out is valued afresh at once), so a
  healthy player's expert rank drifts as real ones do instead of jumping; all three tuned on the real weeks'
  rank churn and expert-vs-projection agreement (sim_eval). The
  preseason experts (week 0 of a season simulated from week 1, sim_season) share the preseason market instead:
  given its ADP (`market`), the players it lists rank by ADP x the same noise (real experts and ADP agree as
  closely: expert vs ADP position ranks of the real top 150, Spearman 0.98 in 2022-2025), the rest after them
  by value x noise.
- `adp`: a draft under the belief on the same values (so a draft room's expert order and ADP agree), in the
  league's scoring, priced on `market_values`. A preseason market also leans on what it saw: with a `record`
  (`track_record`: raw points a game last season) each value is (1 - w) x value + w x record, w = the tuned
  `market.record_weight`; and it pays for a rookie's upside: with a `premium` ({player: multiplier},
  sim_next_year.rookie_premium, fitted on real rookie ADPs) his value is multiplied by it. Skill
  players go by points over a replacement rank per position (the tuned `market.replacement`; for a superflex
  / 2-QB list the QB's is `market.two_qb_replacement`, set so QB1-QB12 go about where the real 2026 2-QB list
  takes them). The blend weight and replacement ranks maximize agreement with the real ADP lists 2022-2025
  (Spearman over the real top 150; each held-out season agrees as well as the fitted ones). Kickers and
  defenses at the real average ADP of the Nth one taken (`kdst_adp` in sim_params.json), stdev = a + b x adp
  as measured. A player believed out for the games left gets none.
- `buzz`: before the week a change starts, HINT_RATE of true breakouts, busts and benchings get a news line;
  false lines are added so that about PRECISION of all lines are true, each about a player the change could
  plausibly happen to, weighted by how likely it is for him (sim_talent.rumors: backups break out, leads who
  are not stars bust, low-rated starting QBs are benched; never a top-STAR_RANK player). sim_season passes no
  false rate outside the weeks a change can start, so those weeks stay quiet.
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
EXPERT_NOISE = 0.08              # expert opinion vs the belief (lognormal sigma; preseason: vs the ADP)
OPINION_SPREAD = {"QB": 0.35, "RB": 1.0, "WR": 1.0, "TE": 1.0, "K": 1.0, "DST": 1.0}   # x EXPERT_NOISE, weekly
OPINION_KEEP = 0.9               # the share of last week's opinion (AR(1) coefficient) a week keeps
WORTH_STEP = 0.4                 # the share of the way from last week's held value to this week's a week moves
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
                scoring: ScoringSettings, lines: Mapping | None = None) -> dict[int, float]:
    """{player: projected points} for every game of `week` (see the module docstring). `lines`: {home team:
    sim_lines.Line} each game is centred on (sim_game.expected's target), as its draw is."""
    found = {}
    for game in schedule.games:
        if game.week != week or game.home_team not in belief.teams or game.away_team not in belief.teams:
            continue
        pregame = (lines or {}).get(game.home_team)
        target = {game.home_team: pregame.home, game.away_team: pregame.away} if pregame is not None else None
        for pid, line in expected(belief, game.home_team, game.away_team, out, scoring, target=target).items():
            if line.points >= MIN_PROJECTION:
                found[pid] = round(line.points, 2)
    return found


def values(belief: World, scoring: ScoringSettings, games_left: int = SEASON_GAMES,
           edges: Mapping[str, float] | None = None) -> dict[int, float]:
    """{player: mean points a game over the next `games_left` games}: a player believed out (`out_weeks`) scores
    nothing in his first out_weeks games, and his teammates share his work meanwhile. `edges`: {team: points}
    its games are centred above the belief's own mean (sim_lines.edges: what the team's scoring says)."""
    phases = sorted({0, games_left} | {min(p.out_weeks, games_left) for p in belief.players.values()})
    found: dict[int, float] = defaultdict(float)
    for team in sorted(belief.teams):
        roster, lines = belief.roster(team), {}          # lines: one expected() per distinct set of sitters
        for start, end in zip(phases, phases[1:]):
            sitting = frozenset(p.player_id for p in roster if p.out_weeks > start)
            if sitting not in lines:
                shift = {team: edges[team]} if edges and team in edges else None
                lines[sitting] = expected(belief, team, None, sitting, scoring, shift=shift)
            for pid, line in lines[sitting].items():
                found[pid] += line.points * (end - start) / max(1, games_left)
    return dict(found)


def opinion(previous: Mapping[int, float], ids: Iterable[int], dice: np.random.Generator) -> dict[int, float]:
    """{player: this week's opinion z (standard normal)}: OPINION_KEEP of last week's plus a fresh draw (a player
    with none yet starts from a fresh one). Always one draw per player, in id order."""
    ids = sorted(ids)
    fresh = dice.standard_normal(len(ids))
    keep = OPINION_KEEP
    return {pid: keep * previous[pid] + np.sqrt(1 - keep ** 2) * z if pid in previous else float(z)
            for pid, z in zip(ids, fresh)}


def held(previous: Mapping[int, float], worth: Mapping[int, float], belief: World) -> dict[int, float]:
    """{player: the value experts rank him on this week}: WORTH_STEP of the way from last week's to this week's
    `worth` for a healthy player who had one; this week's for a new player or one believed out (out_weeks)."""
    return {pid: previous[pid] + WORTH_STEP * (value - previous[pid])
            if pid in previous and belief.players[pid].out_weeks == 0 else value
            for pid, value in worth.items() if pid in belief.players}


def expert_ranks(belief: World, known: Iterable[int], week: int, dice: np.random.Generator, scoring: ScoringSettings,
                 games_left: int, market: Mapping[int, float] | None = None,
                 opinions: Mapping[int, float] | None = None,
                 edges: Mapping[str, float] | None = None,
                 worth: Mapping[int, float] | None = None) -> dict[tuple[int, int], int]:
    """{(player, week): position rank} among the `known` players the belief places on a team. `market`: {player:
    ADP} of a preseason market the experts share (see the module docstring), else they rank on the belief.
    `opinions`: {player: z} carried from last week (`opinion`); None: fresh ones. `edges`: each team's scoring
    edge (`values`), as experts know which offenses score. `worth`: the values to rank on (`held`), else `values`."""
    worth = worth if worth is not None else values(belief, scoring, games_left, edges)
    ids = sorted(pid for pid in set(known) if pid in belief.players)
    if market:
        noise = dict(zip(ids, dice.lognormal(0.0, EXPERT_NOISE, len(ids))))
    else:
        z = opinions if opinions is not None else opinion({}, ids, dice)
        noise = {pid: float(np.exp(EXPERT_NOISE * OPINION_SPREAD.get(belief.players[pid].position, 1.0) * z.get(pid, 0.0)))
                 for pid in ids}
    market = market or {}

    def view(pid: int) -> tuple:              # sorts best first: the listed by noisy pick, then the rest by value
        return (False, market[pid] * noise[pid]) if pid in market else (True, -worth.get(pid, 0.0) * noise[pid])

    ranks = {}
    for position in ("QB", "RB", "WR", "TE", "K", "DST"):
        order = sorted((pid for pid in ids if belief.players[pid].position == position), key=lambda pid: (view(pid), pid))
        ranks.update({(pid, week): rank for rank, pid in enumerate(order, start=1)})
    return ranks


def market_values(world: World, scoring: ScoringSettings, games_left: int = SEASON_GAMES,
                  record: Mapping[int, float] | None = None,
                  premium: Mapping[int, float] | None = None) -> dict[int, float]:
    """{player: the points a game a draft prices him on}: `values`, blended with his `record` (points a game last
    season, `track_record`) when given, x his `premium` (a rookie's, sim_next_year.rookie_premium) when given."""
    season = values(world, scoring, games_left)
    if record:
        weight = params()["market"]["record_weight"]
        season = {pid: (1 - weight) * v + weight * record.get(pid, v) for pid, v in season.items()}
    if premium:
        season = {pid: v * premium.get(pid, 1.0) for pid, v in season.items()}
    return season


def adp(world: World, scoring: ScoringSettings | None = None, two_qb: bool = False,
        games_left: int = SEASON_GAMES, record: Mapping[int, float] | None = None,
        premium: Mapping[int, float] | None = None) -> dict[int, tuple[float, float]]:
    """{player: (adp, stdev)} for a draft held under `world` with `games_left` games to play (see the module
    docstring): the league's `scoring` (default PPR), a superflex / 2-QB league's list when `two_qb`, priced
    on `market_values` (with `record` and `premium` when given)."""
    season = market_values(world, scoring or scoring_for("ppr"), games_left, record, premium)
    return draft_board(world, season, two_qb, games_left)


def draft_board(world: World, season: Mapping[int, float], two_qb: bool = False,
                games_left: int = SEASON_GAMES) -> dict[int, tuple[float, float]]:
    """{player: (adp, stdev)} of a draft on `season` ({player: points a game}, `market_values`)."""
    market = params()["market"]
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


def position_ranks(belief: World) -> dict[int, int]:
    """{player: his rank at his position by the market's value (`values`, half PPR)}: how the public rates him.
    sim_talent's benchings and rumours read it, and sim_next_year_fit fits the benchings on it."""
    worth = values(belief, scoring_for("half"))
    ranks: dict[int, int] = {}
    for position in ("QB", "RB", "WR", "TE"):
        order = sorted((pid for pid, p in belief.players.items() if p.position == position),
                       key=lambda pid: (-worth.get(pid, 0.0), pid))
        ranks.update({pid: rank for rank, pid in enumerate(order, start=1)})
    return ranks


def buzz(changes: Iterable[RoleChange], belief: World, week: int, dice: np.random.Generator,
         false_rate: float, out: Collection[int] = (),
         rumors: Mapping[str, Mapping[int, float]] | None = None) -> dict[tuple[int, int], str]:
    """{(player, week): news line} for `week` (see the module docstring). false_rate: the expected true lines
    per week, from which the false ones are sized. `rumors`: {kind: {player: weight}} (sim_talent.rumors), who
    a false line may be about: a (kind, player) pair is drawn in proportion to its weight, so a false line is
    about a change as plausible for him as the true ones are. Never about a player who is out that week (`out`:
    the week's hard tags, or believed out): a role rumour about a player on IR makes no sense."""
    sidelined = set(out) | {pid for pid, p in belief.players.items() if p.out_weeks > 0}
    notes = {}
    for change in sorted(changes, key=lambda c: c.player_id):
        hinted = change.week == week and change.kind in BUZZ and dice.random() < HINT_RATE
        if hinted and change.player_id not in sidelined:
            notes[(change.player_id, week)] = BUZZ[change.kind]
    pool = [(kind, pid, weight) for kind in sorted(rumors or {}) for pid, weight in sorted(rumors[kind].items())
            if weight > 0 and pid not in sidelined]
    count = int(dice.poisson(false_rate * (1 - PRECISION) / PRECISION))
    if pool and count:
        weights = np.array([weight for _, _, weight in pool])
        for i in dice.choice(len(pool), size=min(count, len(pool)), replace=False, p=weights / weights.sum()):
            kind, pid, _ = pool[int(i)]
            notes.setdefault((pid, week), BUZZ[kind])
    return notes


def expected_hints(changes: float, weeks: int) -> float:
    """True buzz lines per week from the season's expected true changes (sim_talent.expected_changes): sizes
    the false ones."""
    return changes * HINT_RATE / max(1, weeks)
