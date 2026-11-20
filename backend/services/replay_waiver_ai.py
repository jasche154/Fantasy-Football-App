"""Replay League: how CPU managers use the waiver wire. Everything comes from a `Knowledge` (pre-game
numbers for the current week), so no CPU ever sees a result or a projection from the future.

- `cpu_claims` (before the waiver run): every CPU scores one shared short list of unrostered skill players
  with `lineup_gain` (points added to its best lineup over the rest of the season, after the drop) and
  claims a player worth about a lineup point a week. At most MAX_CLAIMS, the second planned on the roster
  as it would be after the first.
- `cpu_free_agency` (after the run): fill an empty roster spot, cover a starting slot that would score
  nothing this week (bye or out), and stream a kicker or defense only for a clear edge. At most
  MAX_FREE_AGENT_MOVES per team, free agents only.
- A CPU drops only one of its DROPPABLE_COUNT least valuable skill players by HEALTHY value, never a
  top-PROTECTED_RANK player or an injured player who was above replacement, and never into a roster that
  can't field a lineup. A kicker or defense is only ever swapped for another one.

There is no dice roll: ties go to the better-ranked player, so the same state gives the same moves.
"""
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from services import lineup_gain, replay_state as rs, replay_values as rv, replay_waivers
from services.replay_pool import SKILL
from services.replay_state import Claim, Settings, State
from services.replay_values import Knowledge, ReplayValue

MAX_BY_VALUE = 16                 # shared candidates: the best unrostered skill players by trade value
MAX_BY_PROJECTION = 2             # plus this many per skill position by this week's projection (8 in all)
CLAIM_GAIN_PER_WEEK = 1.0         # claim when the gain is this many lineup points per (weighted) week left
MAX_CLAIMS = 2                    # per CPU per waiver run
MAX_FREE_AGENT_MOVES = 2          # per CPU per week
DROPPABLE_COUNT = 4               # a CPU only drops one of its lowest skill players by healthy value
PROTECTED_RANK = 100              # never a player in the league's top 100 by healthy value
MIN_FILL_GAIN = 0.0               # covering an empty starting slot must add more than this over the season
STREAM_EDGE = 1.5                 # projected points a free-agent K / DST must beat the team's own by
STREAMED = ("K", "DST")

Move = tuple[int, int | None]     # (player id to add, player id to drop)


@dataclass(frozen=True)
class _Context:
    know: Knowledge
    settings: Settings
    horizon: lineup_gain.Horizon
    projector: lineup_gain.Projector
    protected: frozenset[int]     # the league's top players by healthy value
    claim_gain: float             # the season-weighted gain a claim needs


def _context(know: Knowledge, settings: Settings) -> _Context:
    horizon = rv.horizon(know, settings)
    valued = sorted((v for v in know.values if v.healthy_value > 0), key=lambda v: (-v.healthy_value, v.player_id))
    return _Context(know, settings, horizon, rv.projector(know),
                    frozenset(v.player_id for v in valued[:PROTECTED_RANK]),
                    CLAIM_GAIN_PER_WEEK * sum(horizon.weights.values()))


def candidates(know: Knowledge, available: Iterable[ReplayValue]) -> list[ReplayValue]:
    """The short list every CPU looks at, from the players it may take: the skill players among them,
    MAX_BY_VALUE by trade value (ties: closest to replacement), then MAX_BY_PROJECTION per position by this
    week's projection."""
    free = [v for v in available if v.position in SKILL]
    by_value = sorted(free, key=lambda v: (-v.trade_value, know.replacement[v.position] - v.ros_ppg, v.player_id))
    chosen = by_value[:MAX_BY_VALUE]
    for position in SKILL:
        hot = sorted((v for v in free if v.position == position and v.projection > 0),
                     key=lambda v: (-v.projection, -v.ros_ppg, v.player_id))
        chosen += [v for v in hot[:MAX_BY_PROJECTION] if all(v.player_id != c.player_id for c in chosen)]
    return chosen


def _unrostered(state: State, know: Knowledge, also_taken: Iterable[int] = ()) -> list[ReplayValue]:
    taken = rs.rostered(state) | set(also_taken)
    return [v for v in know.values if v.player_id not in taken]


def droppable(roster: Sequence[ReplayValue], know: Knowledge, protected: frozenset[int]) -> list[ReplayValue]:
    """The skill players a CPU is willing to drop, least valuable first (by healthy value, so an injury
    alone never makes a good player droppable)."""
    def safe(value: ReplayValue) -> bool:
        if value.position not in SKILL or value.player_id in protected:
            return False                              # (replacement levels exist for skill positions only)
        return not (value.out and value.last_projection > know.replacement[value.position])

    spare = sorted((v for v in roster if safe(v)), key=lambda v: (v.healthy_value, v.ros_ppg, -v.player_id))
    return spare[:DROPPABLE_COUNT]


def _legal_drops(roster: Sequence[ReplayValue], add: ReplayValue, drops: Sequence[ReplayValue],
                 ctx: _Context) -> list[ReplayValue]:
    """The drops that still leave a roster able to fill every starting slot once `add` is on it."""
    positions = [v.position for v in roster] + [add.position]

    def fields(out: ReplayValue) -> bool:
        left = list(positions)
        left.remove(out.position)
        return rv.can_field_lineup(left, ctx.settings)

    return [out for out in drops if fields(out)]


def _group(position: str) -> str:
    """Who a newcomer competes with for a starting slot: quarterbacks, or everyone who can play FLEX."""
    return "FLEX" if position in rv.FLEX_POSITIONS else position


def _bars(roster: Sequence[ReplayValue], ctx: _Context) -> tuple[float, dict[int, dict[str, float]]]:
    """(this roster's season points, {week: {group: the points a newcomer must beat to start that week}}).

    The bar is the weakest starter in the newcomer's group, or 0 while one of its slots is unfilled."""
    starters = ctx.settings.starters
    slots = {"QB": starters.get("QB", 0), "FLEX": sum(starters.get(slot, 0) for slot in ("FLEX", *rv.FLEX_POSITIONS))}
    by_id = {v.player_id: v for v in roster}
    base, bars = 0.0, {}
    for week, weight in ctx.horizon.weights.items():
        total, ids = lineup_gain.week_lineup(roster, week, ctx.projector, ctx.know.league)
        base += weight * total
        points: dict[str, list[float]] = {group: [] for group in slots}
        for pid in ids:
            points[_group(by_id[pid].position)].append(ctx.projector.points(by_id[pid], week))
        bars[week] = {group: min(found) if found and len(found) >= slots[group] else 0.0
                      for group, found in points.items()}
    return base, bars


def _ceiling(add: ReplayValue, bars: dict[int, dict[str, float]], ctx: _Context) -> float:
    """The most `add` could add to that roster's season: each week, his points over the starter he could
    push out. Never below the real gain, and far cheaper than building the lineups."""
    group = _group(add.position)
    return sum(weight * max(0.0, ctx.projector.points(add, week) - bars[week][group])
               for week, weight in ctx.horizon.weights.items())


def _best_add(roster: Sequence[ReplayValue], pool: Sequence[ReplayValue], ctx: _Context,
              needed: float) -> tuple[float, ReplayValue, ReplayValue | None] | None:
    """(gain, add, drop) for the candidate that adds most to this roster's season, or None when no
    candidate is worth more than `needed`. With an open roster spot nobody is dropped."""
    full = len(roster) >= ctx.settings.roster_size
    drops = droppable(roster, ctx.know, ctx.protected) if full else []
    if full and not drops:
        return None
    league = ctx.know.league
    base, bars = _bars(roster, ctx)
    weakest = min((v.ros_ppg for v in drops), default=0.0)
    best = None
    ceilings = sorted(((_ceiling(add, bars, ctx), order, add) for order, add in enumerate(pool)),
                      key=lambda row: (-row[0], row[1]))
    for ceiling, _, add in ceilings:
        if ceiling <= (best[0] if best else needed):
            break                                     # nobody further down the list can do better
        if full and max(add.ros_ppg, add.projection) <= weakest:
            continue
        # Without a drop the gain can only be higher, so this one sum rules out most of the rest.
        gain = round(lineup_gain.season_points([*roster, add], ctx.horizon, ctx.projector, league) - base, 2)
        if gain <= needed:
            continue
        out = None
        if full:
            legal = _legal_drops(roster, add, drops, ctx)
            if not legal:
                continue
            gain, out = lineup_gain.best_add(roster, legal, add, ctx.horizon, ctx.projector, league)
        if gain > needed and (best is None or gain > best[0]):
            best = (gain, add, out)
    return best


def _plan_claims(roster: Sequence[ReplayValue], pool: Sequence[ReplayValue], ctx: _Context) -> list[Move]:
    """Up to MAX_CLAIMS claims for one roster, best first; each is scored on the roster as it would be
    after the ones before it."""
    moves: list[Move] = []
    roster, pool = list(roster), list(pool)
    while len(moves) < MAX_CLAIMS:
        best = _best_add(roster, pool, ctx, ctx.claim_gain)
        if best is None:
            break
        _, add, out = best
        moves.append((add.player_id, out.player_id if out else None))
        roster = [v for v in roster if v is not out] + [add]
        pool = [v for v in pool if v is not add]
    return moves


def cpu_claims(state: State, settings: Settings, know: Knowledge, seed: int) -> dict[int, list[Claim]]:
    """Every CPU's waiver claims for this week's run, best first ({team id: claims}; teams with none are
    left out). Hand the result to `replay_waivers.process_claims`. Claim ids start at `state.next_id` and are
    never stored. `seed` is unused (no randomness) and kept so every CPU step has the same call shape."""
    ctx = _context(know, settings)
    pool = candidates(know, _unrostered(state, know))
    claims: dict[int, list[Claim]] = {}
    next_id = state.next_id
    for team in state.teams:
        if team.is_user:
            continue
        moves = _plan_claims([know.by_id[pid] for pid in team.roster], pool, ctx)
        if moves:
            claims[team.id] = [Claim(next_id + n, team.id, add, out) for n, (add, out) in enumerate(moves)]
            next_id += len(moves)
    return claims


# ---------------- free agency ----------------

def _top(players: Sequence[ReplayValue]) -> ReplayValue | None:
    """The best of them this week: by projection, then rest-of-season rate, then the lower id."""
    return min(players, key=lambda v: (-v.projection, -v.ros_ppg, v.player_id), default=None)


def _fill_spot(roster: Sequence[ReplayValue], free: Sequence[ReplayValue], ctx: _Context) -> Move | None:
    """An open roster spot: a position the lineup is missing first, else the skill player who adds most."""
    if len(roster) >= ctx.settings.roster_size:
        return None
    positions = [v.position for v in roster]
    holes = rv.lineup_holes(positions, ctx.settings)
    needs = [v for v in free if rv.lineup_holes([*positions, v.position], ctx.settings) < holes]
    if needs:
        return _top(needs).player_id, None
    pool = candidates(ctx.know, free)
    best = _best_add(roster, pool, ctx, MIN_FILL_GAIN)
    add = best[1] if best else next(iter(pool), None)       # nobody helps the lineup: the most valuable
    return (add.player_id, None) if add else None


def _empty_slots(roster: Sequence[ReplayValue], ctx: _Context) -> list[str]:
    """The skill and FLEX starting slots that would score nothing this week (bye, out or nobody there)."""
    by_id = {v.player_id: v for v in roster}
    lineup = rv.best_lineup(ctx.know, list(by_id), ctx.settings)
    return [slot for slot, wanted in ctx.settings.starters.items() if slot not in STREAMED
            and sum(by_id[pid].projection > 0 for pid in lineup.get(slot, ())) < wanted]


def _fill_slot(roster: Sequence[ReplayValue], free: Sequence[ReplayValue], ctx: _Context) -> Move | None:
    """Cover a starting slot that would be empty this week with the best free agent for it, when that
    still adds to the season after dropping a droppable player."""
    for slot in _empty_slots(roster, ctx):
        eligible = rv.FLEX_POSITIONS if slot == "FLEX" else (slot,)
        add = _top([v for v in free if v.position in eligible and v.projection > 0])
        legal = _legal_drops(roster, add, droppable(roster, ctx.know, ctx.protected), ctx) if add else []
        if not legal:
            continue
        gain, out = lineup_gain.best_add(roster, legal, add, ctx.horizon, ctx.projector, ctx.know.league)
        if gain > MIN_FILL_GAIN:
            return add.player_id, out.player_id
    return None


def _stream(roster: Sequence[ReplayValue], free: Sequence[ReplayValue], ctx: _Context) -> Move | None:
    """Swap a kicker for a kicker or a defense for a defense: only when the team's own projects nothing this
    week, or the free agent beats it by STREAM_EDGE. The bigger improvement of the two positions wins."""
    best: tuple[float, Move] | None = None
    for position in STREAMED:
        own = [v for v in roster if v.position == position]
        add = _top([v for v in free if v.position == position and v.projection > 0])
        if not own or add is None:
            continue
        starter = _top(own)
        spare = max(own, key=lambda v: (-v.projection, -v.ros_ppg, v.player_id))      # the worst of them
        edge = add.projection - starter.projection
        if (starter.projection <= 0 or edge >= STREAM_EDGE) and (best is None or edge > best[0]):
            best = (edge, (add.player_id, spare.player_id))
    return best[1] if best else None


def _next_move(state: State, team_id: int, ctx: _Context) -> Move | None:
    roster = [ctx.know.by_id[pid] for pid in rs.team(state, team_id).roster]
    free = _unrostered(state, ctx.know, state.locked)
    for step in (_fill_spot, _fill_slot, _stream):
        move = step(roster, free, ctx)
        if move is not None:
            return move
    return None


def cpu_free_agency(state: State, settings: Settings, know: Knowledge, seed: int) -> State:
    """Every CPU's free-agent moves for this week, in waiver-priority order (which they do not use up):
    at most MAX_FREE_AGENT_MOVES each, never a player in `state.locked`. Works in any phase; the dropped
    players go on waivers through `replay_state.release`. `seed` is unused (no randomness)."""
    ctx = _context(know, settings)
    cpus = [team_id for team_id in state.waiver_order if not rs.team(state, team_id).is_user]
    for team_id in cpus:
        for _ in range(MAX_FREE_AGENT_MOVES):
            move = _next_move(state, team_id, ctx)
            if move is None:
                break
            state = replay_waivers.execute_move(state, settings, know, team_id, *move)
    return state
