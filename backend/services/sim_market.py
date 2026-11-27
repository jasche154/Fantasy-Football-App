"""Simulated seasons: what the public sees before each week (projections, expert ranks, ADP, buzz).

Everything here reads a BELIEF World (sim_world.estimate), never the truth, except `buzz`, which turns the
truth's dated role changes into a noisy hint.

- `projections`: each active player's mean points in his game that week (sim_game.expected) under the
  belief, with the week's hard-tagged players out, so their teammates' projections rise, centred on the game's
  pregame line (sim_lines) as the game itself is, then pulled toward his projection at his team's typical points
  (`typical_points`: against a league-average opponent, no line): a position's tuned `market.line_follow` share of
  the line's move is kept, as real ESPN / Sleeper projections follow a team's implied total only partly
  (sim_metrics_join.line_follow, real 2021-2025 weeks 12-18: QB 0.47, RB 0.33, WR 0.16, TE 0.17; the full
  expectation followed it at 0.79 / 0.78 / 0.43 / 0.43, so its weekly projections churned more than real ones).
  Kept above MIN_PROJECTION, like the real tables (a player out or on bye has none).
- `values`: mean points a game over the games left against a league-average opponent: a player believed out
  (`out_weeks`) scores nothing in his first out_weeks games and his active teammates share his work meanwhile.
- `expert_ranks`: position ranks by value (in a simulated week, with each team's scoring edge from the line
  model, sim_lines.edges: experts know which offenses score, as the week's projections do) x a lognormal opinion among the players known that week: sigma
  EXPERT_NOISE x the position's `market.opinion_spread`, so the noise moves ranks about as much at every
  position (QB values are bunched: the same 8% that barely moves a running back reshuffles the QBs). A simulated
  season carries each player's opinion from week to week (`opinion`: an AR(1), each week keeping
  `market.opinion_keep` of the last and a fresh draw for the rest), and the value they rank a healthy player on
  moves only `market.worth_step` of the way to this week's (`held`: experts revise slowly), except on injury
  news: a player believed out now, or whose own or a same-position teammate's status (out or not) changed since
  last week, is valued afresh at once (a backup whose starter just went on IR, a star back from injury). A
  fill-in QB (replay_fillin.without_news: the trade chart's one point-in-time definition) is ranked no better than FILL_IN_BEST, the best rank real experts gave a fill-in after 2-4 starts (`held_back`; his value
  already counts only the games he is expected to start, until the holder's believed absence ends). The
  three knobs live in sim_params.json's tuned `market` section (`sim_eval --set market.worth_step=...`); they
  were set by hand against the real weeks' rank churn and expert-vs-projection agreement in sim_eval's report
  (healthy top-16 QB churn: real 1.05-1.15 places a week; QB noise at a third of the others' because QB
  values are bunched). The
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
  defenses, and on a 1-QB list the quarterbacks, at the real average ADP of the Nth one taken (`kdst_adp` and
  `qb_adp` in sim_params.json, 1-QB PPR lists 2021-2025; the Nth by value is the Nth taken), stdev = a + b x adp
  as measured. A game a player left hurt does not count in his record (`left_hurt`). A player believed out for the games left gets none.
- `buzz`: before the week a change starts, HINT_RATE of true breakouts, busts and benchings get a news line;
  false lines are added so that about PRECISION of all lines are true, each about a player the change could
  plausibly happen to, weighted by how likely it is for him (sim_talent.rumors: backups break out, leads who
  are not stars bust, low-rated starting QBs are benched; never a top-STAR_RANK player). sim_season passes no
  false rate outside the weeks a change can start, so those weeks stay quiet.
"""
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping
from dataclasses import replace
from types import MappingProxyType

import numpy as np

from services.replay_pool import SeasonData
from services.schedule_strength import Schedule
from services.scoring import ScoringSettings, scoring_for
from services.sim_game import expected
from services.sim_world import RoleChange, World, params

MIN_PROJECTION = 0.1
EXPERT_NOISE = 0.08              # expert opinion vs the belief (lognormal sigma; preseason: vs the ADP)
OPINION_CAP = 3.0                # implied_opinions: no player's seeded opinion beyond 3 sd
FILL_IN_BEST = 16                # the best weekly expert rank of a fill-in QB: real 2020-2025 runs of 3+ starts, after
                                 # 2-4 starts, never ranked better (Flacco 2023, Dobbs 2023: QB16; median 29-32)
OPINION_DEFAULTS = {"opinion_spread": {"QB": 0.35, "RB": 1.0, "WR": 1.0, "TE": 1.0, "K": 1.0, "DST": 1.0},
                    "opinion_keep": 0.9, "worth_step": 0.4, "fill_in_trust": 1.0,
                    "projection_scale": {}, "join_scale": {}, "join_fade": 0.5,
                    "line_follow": {}}                            # defaults of the tuned `market` knobs


def published(fresh: Mapping[int, float], belief: World, since_join: int | None = None) -> dict[int, float]:
    """The week's projections as the public reads them: each x its position's scale. Real sources project high
    (2019-2025: actual - projection QB -0.49, RB -0.50, WR -0.19, TE +0.03, a near-constant 2-6% at every level; the
    belief's own are unbiased): `market.projection_scale` is set so the published table misses results by as much.
    At a real/simulated join (`since_join`: weeks since the first simulated one of a season that began real; None
    for a season simulated whole) the first simulated week continues the real sources' level for the players at
    the top, about 9% above the belief's (sim_eval mid's "join proj ratio"; `market.join_scale`), and the scale
    fades from there to `projection_scale` by `market.join_fade` a week: one scale for both overshot the results
    by 0.66 a projection against a real 0.35. The public's table only, never the truth or the values experts and
    the market rank on."""
    scale, join, fade = (opinion_knob(name) for name in ("projection_scale", "join_scale", "join_fade"))
    weight = float(fade) ** since_join if since_join is not None else 0.0
    out = {}
    for pid, p in fresh.items():
        position = belief.players[pid].position if pid in belief.players else ""
        base = float(scale.get(position, 1.0))
        out[pid] = p * (base + (float(join.get(position, base)) - base) * weight)
    return out


def opinion_knob(name: str):
    """A tuned `market` knob (opinion_spread, opinion_keep, worth_step, fill_in_trust, projection_scale, join_scale,
    join_fade, line_follow)."""
    return params()["market"].get(name, OPINION_DEFAULTS[name])
EXPERT_AGE = {"pre": 6, "ros": 2}     # days before the Sunday, as the real snapshots are (week 0 / later weeks)
SEASON_GAMES = 17
# The replacement rank at each position: defaults, overlaid by the tuned `market.replacement`.
REPLACEMENT = {"QB": 26, "RB": 32, "WR": 38, "TE": 14}
ADP_SKILL = 270                  # skill players on a draft board
MIN_ADP_STDEV = 0.5
RECORD_MIN_GAMES = 4             # a track record needs this many games
LEFT_HURT_SNAP = 50.0            # % of the snaps under which a game followed by a hard tag was one he left hurt
HINT_RATE = 0.6
PRECISION = 0.6
BUZZ = {"breakout": "Beat reporters say his role is growing; expect more work.",
        "bust": "Reports say his snaps and touches are shrinking.",
        "bench": "Reports say his starting job is in danger."}


def typical_points(belief: World, team: str, out: Collection[int]) -> float:
    """`team`'s mean points against a league-average opponent under `belief` with `out` out: what its line would
    say with no matchup in it (a whole game's mean, as the centring reads an uncentred side's)."""
    from services.sim_game import _mean_points, _side_means, _side_of
    return _mean_points(_side_means(_side_of(belief, team, None, out)), None)


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
        full = expected(belief, game.home_team, game.away_team, out, scoring, target=target, week=week)
        own = (expected(belief, game.home_team, game.away_team, out, scoring, week=week,
                        target={team: typical_points(belief, team, out) for team in target}) if target else full)
        follow = opinion_knob("line_follow")
        for pid, line in full.items():
            share = follow.get(belief.players[pid].position, 1.0) if pid in belief.players else 1.0
            points = own[pid].points + share * (line.points - own[pid].points) if pid in own else line.points
            if points >= MIN_PROJECTION:
                found[pid] = round(points, 2)
    return found


def values(belief: World, scoring: ScoringSettings, games_left: int = SEASON_GAMES,
           edges: Mapping[str, float] | None = None) -> dict[int, float]:
    """{player: mean points a game over the next `games_left` games}: a player believed out (`out_weeks`) scores
    nothing in his first out_weeks games, and his teammates share his work meanwhile. `edges`: {team: points}
    its games are centred above the belief's own mean (sim_lines.edges: what the team's scoring says)."""
    from services.sim_centre import team_factor
    phases = sorted({0, games_left} | {min(p.out_weeks, games_left) for p in belief.players.values()})
    found: dict[int, float] = defaultdict(float)
    for team in sorted(belief.teams):
        roster, lines = belief.roster(team), {}          # lines: one expected() per distinct set of sitters
        factors = None
        if edges and team in edges:                      # one factor per team, from its first set of sitters
            first = frozenset(p.player_id for p in roster if p.out_weeks > 0)
            factors = {team: team_factor(belief, team, first, edges[team])}
        for start, end in zip(phases, phases[1:]):
            sitting = frozenset(p.player_id for p in roster if p.out_weeks > start)
            if sitting not in lines:
                lines[sitting] = expected(belief, team, None, sitting, scoring, factors=factors)
            for pid, line in lines[sitting].items():
                found[pid] += line.points * (end - start) / max(1, games_left)
    return dict(found)


def fill_in_view(belief: World, chosen: Collection[int]) -> World:
    """The public belief with each of `chosen` (replay_fillin.without_news) keeping only, of his passing quality above a backup's,
    `market.fill_in_trust` of its lead: a few games behind another QB's job do not make him a starter's passer
    in the public's eyes (see the module docstring). Never applied to the truth."""
    trust, backup = float(opinion_knob("fill_in_trust")), params()["league"]["backup_quality"]
    if not chosen or trust >= 1.0:
        return belief
    players = dict(belief.players)
    for pid in chosen:
        quality = players[pid].quality
        players[pid] = replace(players[pid], quality=min(quality, backup + trust * (quality - backup)))
    return World(MappingProxyType(players), belief.teams, belief.changes)


def held_back(order: list[int], chosen: set[int], best: int) -> list[int]:
    """`order` (best first) with each of `chosen` ranked no better than `best`, the others in their order."""
    early = [pid for pid in order[:best - 1] if pid in chosen]
    if not early:
        return order
    others = [pid for pid in order if pid not in early]
    return others[:best - 1] + early + others[best - 1:]


def opinion(previous: Mapping[int, float], ids: Iterable[int], dice: np.random.Generator) -> dict[int, float]:
    """{player: this week's opinion z (standard normal)}: opinion_keep of last week's plus a fresh draw (a player
    with none yet starts from a fresh one). Always one draw per player, in id order."""
    ids = sorted(ids)
    fresh = dice.standard_normal(len(ids))
    keep = float(opinion_knob("opinion_keep"))
    return {pid: keep * previous[pid] + np.sqrt(1 - keep ** 2) * z if pid in previous else float(z)
            for pid, z in zip(ids, fresh)}


def implied_opinions(belief: World, known: SeasonData, week: int, worth: Mapping[int, float]) -> dict[int, float]:
    """{player: the opinion z under which ranking on `worth` (expert_ranks) gives `known`'s expert ranks of `week`}
    at each position: the player the real experts ranked r-th gets the r-th best worth among those ranked. Seeds
    the experts' memory at a mid-season join (the last real week's ranks), so the first simulated ranks carry
    them on (opinion_keep) instead of starting from fresh opinions. Within +-OPINION_CAP."""
    spread = opinion_knob("opinion_spread")
    out = {}
    for position in ("QB", "RB", "WR", "TE", "K", "DST"):
        ranked = sorted((rank, pid) for (pid, w), rank in known.expert_ranks.items() if w == week
                        and pid in belief.players and belief.players[pid].position == position and worth.get(pid, 0) > 0)
        levels = sorted((worth[pid] for _, pid in ranked), reverse=True)
        scale = EXPERT_NOISE * spread.get(position, 1.0)
        for i, ((_, pid), level) in enumerate(zip(ranked, levels)):      # a hair apart: equal levels keep the order
            out[pid] = float(np.clip(np.log(level * (1 - 1e-6 * i) / worth[pid]) / scale, -OPINION_CAP, OPINION_CAP))
    return out


def expert_lean(belief: World, known: SeasonData, week: int, scoring: ScoringSettings, games_left: int) -> dict[int, float]:
    """{player: price multiplier} for a market held mid-season (sim_setup._market_adp): the opinion under which the
    belief's values give `known`'s expert ranks of `week` (`implied_opinions`), as the experts' noise prices it.
    The simulated experts carry the last real ranks over the join; a market on the belief alone left a jump-in
    draft room's order and its ADP agreeing at 0.77-0.80 (Spearman, top 60) where one belief gave 0.88-0.91."""
    worth = values(belief, scoring, games_left)
    spread = opinion_knob("opinion_spread")
    return {pid: float(np.exp(EXPERT_NOISE * spread.get(belief.players[pid].position, 1.0) * z))
            for pid, z in implied_opinions(belief, known, week, worth).items()}


def held(previous: Mapping[int, float], worth: Mapping[int, float], belief: World,
         was_out: Collection[int] = (), benched: Collection[int] = ()) -> dict[int, float]:
    """{player: the value experts rank him on this week}: worth_step of the way from last week's to this week's
    `worth` for a healthy player who had one; this week's in full for a new player, one believed out, or one in a
    position group (team, position) where somebody's status changed since last week (`was_out`: who was believed
    out then; `benched`: a QB who lost his job this week, sim_qb_jobs.decide, news like an injury)."""
    step, was_out = float(opinion_knob("worth_step")), set(was_out)
    news = {(p.team, p.position) for pid, p in belief.players.items() if (p.out_weeks > 0) != (pid in was_out)}
    news |= {(belief.players[pid].team, belief.players[pid].position) for pid in benched if pid in belief.players}
    out = {}
    for pid, value in worth.items():
        player = belief.players.get(pid)
        if player is None:
            continue
        settled = pid in previous and player.out_weeks == 0 and (player.team, player.position) not in news
        out[pid] = previous[pid] + step * (value - previous[pid]) if settled else value
    return out


def expert_ranks(belief: World, known: Iterable[int], week: int, dice: np.random.Generator, scoring: ScoringSettings,
                 games_left: int, market: Mapping[int, float] | None = None,
                 opinions: Mapping[int, float] | None = None,
                 edges: Mapping[str, float] | None = None,
                 worth: Mapping[int, float] | None = None, filling: Collection[int] = ()) -> dict[tuple[int, int], int]:
    """{(player, week): position rank} among the `known` players the belief places on a team. `market`: {player:
    ADP} of a preseason market the experts share (see the module docstring), else they rank on the belief.
    `opinions`: {player: z} carried from last week (`opinion`); None: fresh ones. `edges`: each team's scoring
    edge (`values`), as experts know which offenses score. `worth`: the values to rank on (`held`), else `values`.
    `filling`: the fill-in QBs (replay_fillin.without_news), ranked no better than FILL_IN_BEST."""
    worth = worth if worth is not None else values(belief, scoring, games_left, edges)
    ids = sorted(pid for pid in set(known) if pid in belief.players)
    if market:
        noise = dict(zip(ids, dice.lognormal(0.0, EXPERT_NOISE, len(ids))))
    else:
        z = opinions if opinions is not None else opinion({}, ids, dice)
        spread = opinion_knob("opinion_spread")
        noise = {pid: float(np.exp(EXPERT_NOISE * spread.get(belief.players[pid].position, 1.0) * z.get(pid, 0.0)))
                 for pid in ids}
    market = market or {}

    def view(pid: int) -> tuple:              # sorts best first: the listed by noisy pick, then the rest by value
        return (False, market[pid] * noise[pid]) if pid in market else (True, -worth.get(pid, 0.0) * noise[pid])

    filling = set(filling)
    ranks = {}
    for position in ("QB", "RB", "WR", "TE", "K", "DST"):
        order = sorted((pid for pid in ids if belief.players[pid].position == position), key=lambda pid: (view(pid), pid))
        if position == "QB":
            order = held_back(order, filling, FILL_IN_BEST)
        ranks.update({(pid, week): rank for rank, pid in enumerate(order, start=1)})
    return ranks


def market_values(world: World, scoring: ScoringSettings, games_left: int = SEASON_GAMES,
                  record: Mapping[int, float] | None = None,
                  premium: Mapping[int, float] | None = None,
                  qb_share: Mapping[int, float] | None = None) -> dict[int, float]:
    """{player: the points a game a draft prices him on}: `values`, blended with his `record` (points a game last
    season, `track_record`) when given, x his `premium` (a rookie's, sim_next_year.rookie_premium) when given. A
    QB in `qb_share` (a rookie behind the starter, sim_next_year.rookie_qb_share) at least that share of his
    points a game as the starter: the market prices the starts he is expected to take over."""
    season = values(world, scoring, games_left)
    for pid, share in (qb_share or {}).items():
        if pid in world.players and share > 0:
            season[pid] = max(season.get(pid, 0.0), share * _as_starter(world, pid, scoring))
    if record:
        weight = params()["market"]["record_weight"]
        season = {pid: (1 - weight) * v + weight * record.get(pid, v) for pid, v in season.items()}
    if premium:
        season = {pid: v * premium.get(pid, 1.0) for pid, v in season.items()}
    return season


def _as_starter(world: World, pid: int, scoring: ScoringSettings) -> float:
    """His points a game in a healthy week as his team's starting QB."""
    player = world.players[pid]
    top = max(p.starts for p in world.roster(player.team) if p.position == "QB")
    lifted = World(MappingProxyType({**world.players, pid: replace(player, starts=top + 1.0)}), world.teams, world.changes)
    line = expected(lifted, player.team, None, frozenset(), scoring).get(pid)
    return line.points if line else 0.0


def adp(world: World, scoring: ScoringSettings | None = None, two_qb: bool = False,
        games_left: int = SEASON_GAMES, record: Mapping[int, float] | None = None,
        premium: Mapping[int, float] | None = None,
        qb_share: Mapping[int, float] | None = None) -> dict[int, tuple[float, float]]:
    """{player: (adp, stdev)} for a draft held under `world` with `games_left` games to play (see the module
    docstring): the league's `scoring` (default PPR), a superflex / 2-QB league's list when `two_qb`, priced
    on `market_values` (with `record` and `premium` when given)."""
    season = market_values(world, scoring or scoring_for("ppr"), games_left, record, premium, qb_share)
    return draft_board(world, season, two_qb, games_left)


def draft_board(world: World, season: Mapping[int, float], two_qb: bool = False,
                games_left: int = SEASON_GAMES) -> dict[int, tuple[float, float]]:
    """{player: (adp, stdev)} of a draft on `season` ({player: points a game}, `market_values`)."""
    market = params()["market"]
    healthy = {pid: v * SEASON_GAMES for pid, v in season.items() if world.players[pid].out_weeks < games_left}
    intercept, slope = params()["adp_stdev"]
    board: dict[int, float] = {}
    # a 1-QB list takes QBs as real ones do (QB24 near pick 160): points over a 12th-QB baseline would sink QB20+ past
    # every deep RB and WR, the last starters off the board
    curves = {**params()["kdst_adp"], **({} if two_qb else {"QB": params()["qb_adp"]})}
    for position, picks in curves.items():
        ranked = sorted((pid for pid in healthy if world.players[pid].position == position), key=lambda p: (-healthy[p], p))
        board.update({pid: picks[i] for i, pid in enumerate(ranked[:len(picks)])})
    baseline = {}
    replacement = {**REPLACEMENT, **market.get("replacement", {})}
    replacement = {**replacement, "QB": market["two_qb_replacement"]} if two_qb else replacement
    for position, rank in replacement.items():
        points = sorted((v for pid, v in healthy.items() if world.players[pid].position == position), reverse=True)
        baseline[position] = points[min(rank, len(points)) - 1] if points else 0.0
    skill = sorted((pid for pid in healthy if world.players[pid].position in REPLACEMENT
                    and world.players[pid].position not in curves),
                   key=lambda p: (-(healthy[p] - baseline[world.players[p].position]), p))[:ADP_SKILL]
    taken = {round(v) for v in board.values()}
    slot = 0
    for pid in skill:
        slot += 1
        while slot in taken:
            slot += 1
        board[pid] = float(slot)
    return {pid: (round(value, 1), round(max(MIN_ADP_STDEV, intercept + slope * value), 1)) for pid, value in board.items()}


def left_hurt(data: SeasonData, pid: int, week: int) -> bool:
    """He left `week`'s game hurt: under LEFT_HURT_SNAP % of the snaps, and a hard tag in one of the next two weeks
    (a bye between). No snap counts: never."""
    usage = data.usage.get((pid, week))
    if usage is None or usage.snap_pct is None or usage.snap_pct >= LEFT_HURT_SNAP:
        return False
    return any((tag := data.injuries.get((pid, later))) is not None and tag.hard for later in (week + 1, week + 2))


def track_record(data: SeasonData) -> dict[int, float]:
    """{player: points a game over his games in `data`} for players with RECORD_MIN_GAMES or more (the market's
    memory of a season: raw production, not regressed), leaving out a game he left hurt (`left_hurt`: Lamar
    Jackson's 2% of 2026 week 8's snaps is no 0-point game)."""
    totals: dict[int, list[float]] = defaultdict(list)
    for (pid, week), points in data.actual.items():
        if not left_hurt(data, pid, week):
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
