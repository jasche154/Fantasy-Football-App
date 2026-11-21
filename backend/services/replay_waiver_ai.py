"""Replay League: how CPU managers use the waiver wire. Everything comes from a `Knowledge` (pre-game
numbers for the current week), so no CPU ever sees a result or a projection from the future.

- `cpu_claims` (before the waiver run): every CPU scores one shared short list of unrostered skill players
  with `lineup_gain` (points added to its best lineup over the rest of the season, after the drop) and
  claims a player worth about a lineup point a week. At most MAX_CLAIMS, the second planned on the roster
  as it would be after the first.
- `cpu_free_agency` (after the run), free agents only: first cover every starting slot (kicker and
  defense included) that nobody on the roster can fill with a projection this week (bye or out), whatever
  that costs later. Then, up to MAX_FREE_AGENT_MOVES moves in all: fill an empty roster spot, get back
  under a positional limit, and swap a kicker or defense who does play only for STREAM_EDGE projected points.
- A CPU never drops a top-PROTECTED_RANK player (by HEALTHY value) or an injured player who was above
  replacement (except, to cover a slot nobody plays in while its IR slot is taken, one tagged IR or PUP
  outside the top PROTECTED_RANK), and never into a roster that can't field a lineup. For a claim or an
  open spot it drops only one of its DROPPABLE_COUNT least valuable skill players. A kicker or defense is
  only ever swapped for another one.
- No add takes a CPU over its positional limit (`replay_values.crowded`: 2 QB, 2 TE, 1 K, 1 DST, 6 RB,
  7 WR) unless nobody it has there plays this week, and it lets the extra one go again afterwards.
- `cpu_ir` (before claims and free agency): a CPU brings back an IR player who lost his tag (into an open
  spot, swapping in an eligible player worth keeping, or cutting one it can spare, maybe him), then puts its
  best eligible player worth keeping on IR. Size and this week's lineup read the active roster only; season
  points and positional limits also count the IR player (`_Context.stash`): he is coming back.

There is no dice roll: ties go to the better-ranked player, so the same state gives the same moves.
"""
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from services import lineup_gain, replay_ir, replay_state as rs, replay_values as rv, replay_waivers
from services.replay_pool import SKILL
from services.replay_state import Claim, Settings, State
from services.replay_values import Knowledge, ReplayValue

MAX_BY_VALUE = 16                 # shared candidates: the best unrostered skill players by trade value
MAX_BY_PROJECTION = 2             # plus this many per skill position by this week's projection (8 in all)
CLAIM_GAIN_PER_WEEK = 1.0         # claim when the gain is this many lineup points per (weighted) week left
MAX_CLAIMS = 2                    # per CPU per waiver run
MAX_FREE_AGENT_MOVES = 2          # per CPU per week (covering a starting slot is always allowed)
DROPPABLE_COUNT = 4               # a CPU only drops one of its lowest skill players by healthy value
PROTECTED_RANK = 100              # never a player in the league's top 100 by healthy value
MIN_FILL_GAIN = 0.0               # a player for an open roster spot must add more than this over the season
STREAM_EDGE = 4.0                 # projected points a free-agent K / DST must beat the team's own by (real
                                  # seasons: a league's CPUs make about 56 such swaps a season at 2.0, nearly
                                  # all of them defenses chasing a matchup, 28 at 3.0 and 12 at 4.0)
STREAMED = ("K", "DST")
LONG_TAGS = ("IR", "PUP")         # out for most of what is left (replay_values.IR_OUT_SHARE)

Move = tuple[int, int | None]     # (player id to add, player id to drop)


@dataclass(frozen=True)
class _Context:
    know: Knowledge
    settings: Settings
    horizon: lineup_gain.Horizon
    projector: lineup_gain.Projector
    protected: frozenset[int]     # the league's top players by healthy value
    claim_gain: float             # the season-weighted gain a claim needs
    stash: tuple[ReplayValue, ...] = ()     # the IR players of the team being planned for


def _for_team(ctx: _Context, team: rs.Team) -> _Context:
    return replace(ctx, stash=tuple(ctx.know.by_id[pid] for pid in team.ir))


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


def _spare(roster: Sequence[ReplayValue], know: Knowledge, protected: frozenset[int]) -> list[ReplayValue]:
    """Every skill player a CPU could bear to lose, least valuable first (by healthy value, so an injury
    alone never makes a good player droppable)."""
    def safe(value: ReplayValue) -> bool:
        if value.position not in SKILL or value.player_id in protected:
            return False                              # (replacement levels exist for skill positions only)
        return not (value.out and value.last_projection > know.replacement[value.position])

    return sorted((v for v in roster if safe(v)), key=lambda v: (v.healthy_value, v.ros_ppg, -v.player_id))


def droppable(roster: Sequence[ReplayValue], know: Knowledge, protected: frozenset[int]) -> list[ReplayValue]:
    """The DROPPABLE_COUNT skill players a CPU is willing to drop for an add, least valuable first."""
    return _spare(roster, know, protected)[:DROPPABLE_COUNT]


def _legal_drops(roster: Sequence[ReplayValue], add: ReplayValue, drops: Sequence[ReplayValue],
                 ctx: _Context) -> list[ReplayValue]:
    """The drops that leave the roster, once `add` is on it, able to fill every starting slot and within
    its positional limit (a like-for-like swap never adds to a position)."""
    def allowed(out: ReplayValue) -> bool:
        left = [v for v in roster if v is not out]
        return (rv.can_field_lineup([*(v.position for v in left), add.position], ctx.settings)
                and (out.position == add.position
                     or not rv.crowded([*left, *ctx.stash], [add], add.position, ctx.settings)))

    return [out for out in drops if allowed(out)]


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
    league, owned = ctx.know.league, [*roster, *ctx.stash]      # value and limits count the IR player
    base, bars = _bars(owned, ctx)
    weakest = min((v.ros_ppg for v in drops), default=0.0)
    best = None
    ceilings = sorted(((_ceiling(add, bars, ctx), order, add) for order, add in enumerate(pool)),
                      key=lambda row: (-row[0], row[1]))
    for ceiling, _, add in ceilings:
        if ceiling <= (best[0] if best else needed):
            break                                     # nobody further down the list can do better
        if full and max(add.ros_ppg, add.projection) <= weakest:
            continue
        if not full and rv.crowded(owned, [add], add.position, ctx.settings):
            continue
        # Without a drop the gain can only be higher, so this one sum rules out most of the rest.
        gain = round(lineup_gain.season_points([*owned, add], ctx.horizon, ctx.projector, league) - base, 2)
        if gain <= needed:
            continue
        out = None
        if full:
            legal = _legal_drops(roster, add, drops, ctx)
            if not legal:
                continue
            gain, out = lineup_gain.best_add(owned, legal, add, ctx.horizon, ctx.projector, league)
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
        moves = _plan_claims([know.by_id[pid] for pid in team.roster], pool, _for_team(ctx, team))
        if moves:
            claims[team.id] = [Claim(next_id + n, team.id, add, out) for n, (add, out) in enumerate(moves)]
            next_id += len(moves)
    return claims


# ---------------- free agency ----------------

def _this_week(value: ReplayValue) -> tuple[float, float, int]:
    """Sorts best first for this week: by projection, then rest-of-season rate, then the lower id."""
    return -value.projection, -value.ros_ppg, value.player_id


def _top(players: Iterable[ReplayValue]) -> ReplayValue | None:
    return min(players, key=_this_week, default=None)


def _worst_own(roster: Sequence[ReplayValue], add: ReplayValue) -> ReplayValue | None:
    """The kicker or defense a new one replaces: a CPU never carries a spare."""
    return max((v for v in roster if v.position == add.position and v.position in STREAMED), key=_this_week,
               default=None)


def _cover(roster: Sequence[ReplayValue], free: Sequence[ReplayValue], ctx: _Context) -> Move | None:
    """A starting slot nobody on the roster can fill with a projection this week gets the best free agent
    for it, whatever that costs later. He replaces the team's own kicker or defense, or takes an open
    roster spot, or the place of the least valuable player it can spare whose going leaves one slot fewer
    without a starter (so never a swap that only moves the gap). When its IR slot is taken, a player tagged
    LONG_TAGS outside the top players may go too, after those it can spare."""
    ids = [v.player_id for v in roster]
    idle = rv.idle_slots(ctx.know, ids, ctx.settings)
    spare = _spare(roster, ctx.know, ctx.protected)
    gone = sorted((v for v in roster if ctx.stash and v.position in SKILL and v.player_id not in ctx.protected
                   and v not in spare and v.injury is not None and v.injury.tag in LONG_TAGS),
                  key=lambda v: (v.healthy_value, v.ros_ppg, -v.player_id))
    # Not counting ctx.stash in either branch: this slot has nobody who plays, and an IR player there would
    # use up crowded's allowance for it (two QBs on bye and one on IR would leave the slot empty).
    bare = replace(ctx, stash=())
    for slot in idle:
        eligible = rv.FLEX_POSITIONS if slot == "FLEX" else (slot,)
        best = (_top(v for v in free if v.position == position and v.projection > 0) for position in eligible)
        for add in sorted(filter(None, best), key=_this_week):
            own = _worst_own(roster, add)
            if own:
                return add.player_id, own.player_id
            if len(roster) < ctx.settings.roster_size:
                if rv.crowded(roster, [add], add.position, ctx.settings):
                    continue
                return add.player_id, None
            for out in _legal_drops(roster, add, [*spare, *gone], bare):
                after = [pid for pid in ids if pid != out.player_id] + [add.player_id]
                if len(rv.idle_slots(ctx.know, after, ctx.settings)) < len(idle):
                    return add.player_id, out.player_id
    return None


def _fill_spot(roster: Sequence[ReplayValue], free: Sequence[ReplayValue], ctx: _Context) -> Move | None:
    """An open roster spot: a position the lineup is missing first, else the skill player who adds most."""
    if len(roster) >= ctx.settings.roster_size:
        return None
    positions = [v.position for v in roster]
    holes = rv.lineup_holes(positions, ctx.settings)
    needs = [v for v in free if rv.lineup_holes([*positions, v.position], ctx.settings) < holes]
    if needs:
        return _top(needs).player_id, None
    pool = [v for v in candidates(ctx.know, free)
            if not rv.crowded([*roster, *ctx.stash], [v], v.position, ctx.settings)]
    best = _best_add(roster, pool, ctx, MIN_FILL_GAIN)
    add = best[1] if best else next(iter(pool), None)       # nobody helps the lineup: the most valuable
    return (add.player_id, None) if add else None


def _trim(roster: Sequence[ReplayValue], free: Sequence[ReplayValue], ctx: _Context) -> Move | None:
    """Back to the positional limit after a week that needed an extra player: the least valuable one it can
    spare at an over-full position goes (never one a starting slot needs this week) for whoever fills the
    open spot best, as long as that is no step down in value."""
    idle = len(rv.idle_slots(ctx.know, [v.player_id for v in roster], ctx.settings))
    held = Counter(v.position for v in (*roster, *ctx.stash))
    for out in _spare(roster, ctx.know, ctx.protected):
        left = [v for v in roster if v is not out]
        if (held[out.position] > rv.position_cap(out.position, ctx.settings)
                and len(rv.idle_slots(ctx.know, [v.player_id for v in left], ctx.settings)) <= idle):
            move = _fill_spot(left, free, ctx)
            if move is not None and ctx.know.by_id[move[0]].healthy_value >= out.healthy_value:
                return move[0], out.player_id
    return None


def _stream(roster: Sequence[ReplayValue], free: Sequence[ReplayValue], ctx: _Context) -> Move | None:
    """Swap a kicker for a kicker or a defense for a defense when the free agent beats the team's own by
    STREAM_EDGE this week (one who does not play at all is `_cover`'s job). The bigger edge wins."""
    best: tuple[float, Move] | None = None
    for position in STREAMED:
        add = _top(v for v in free if v.position == position and v.projection > 0)
        own = _worst_own(roster, add) if add else None
        if own is None:
            continue
        edge = add.projection - _top(v for v in roster if v.position == position).projection
        if edge >= STREAM_EDGE and (best is None or edge > best[0]):
            best = (edge, (add.player_id, own.player_id))
    return best[1] if best else None


def _next_move(state: State, team_id: int, ctx: _Context, made: int) -> Move | None:
    team = rs.team(state, team_id)
    roster, ctx = [ctx.know.by_id[pid] for pid in team.roster], _for_team(ctx, team)
    free = _unrostered(state, ctx.know, state.locked)
    steps = (_cover, _fill_spot, _trim, _stream) if made < MAX_FREE_AGENT_MOVES else (_cover,)
    return next((move for step in steps if (move := step(roster, free, ctx)) is not None), None)


def cpu_free_agency(state: State, settings: Settings, know: Knowledge, seed: int) -> State:
    """Every CPU's free-agent moves for this week, in waiver-priority order (which they do not use up):
    never a player in `state.locked`, and at most MAX_FREE_AGENT_MOVES each unless a starting slot still
    has nobody who plays. Works in any phase; the dropped players go on waivers through
    `replay_state.release`. `seed` is unused (no randomness)."""
    ctx = _context(know, settings)
    cpus = [team_id for team_id in state.waiver_order if not rs.team(state, team_id).is_user]
    for team_id in cpus:
        if replay_ir.blocked_reason(state, know, team_id):            # execute_move would refuse every add
            continue
        for made in range(sum(settings.starters.values())):           # one per starting slot at the very most
            move = _next_move(state, team_id, ctx, made)
            if move is None:
                break
            state = replay_waivers.execute_move(state, settings, know, team_id, *move)
    return state


# ---------------- the IR slot ----------------

def _stash_pick(roster: Sequence[ReplayValue], ctx: _Context) -> ReplayValue | None:
    """The best eligible skill player the team would never spare (top player, or above replacement)."""
    keep = [v for v in roster if v.position in SKILL and replay_ir.eligible(ctx.know, v.player_id)
            and (v.player_id in ctx.protected or v.last_projection > ctx.know.replacement[v.position])]
    return max(keep, key=lambda v: (v.healthy_value, v.ros_ppg, -v.player_id), default=None)


def _bring_back(state: State, ctx: _Context, team: rs.Team, player_id: int) -> State:
    """IR-18: an IR player who lost his tag comes back into an open spot, swapped for an eligible player
    worth keeping, or for the least valuable player it can spare (maybe himself). Else it stays blocked."""
    settings, know = ctx.settings, ctx.know
    if len(team.roster) < settings.roster_size:
        return replay_ir.activate(state, settings, know, team.id, player_id)
    roster = [know.by_id[pid] for pid in team.roster]
    pick = _stash_pick(roster, ctx)
    if pick is not None:
        return replay_ir.activate(state, settings, know, team.id, player_id, swap=pick.player_id)
    group = [*roster, know.by_id[player_id]]
    for cut in _spare(group, know, ctx.protected):
        if rv.can_field_lineup([v.position for v in group if v is not cut], settings):
            if cut.player_id == player_id:
                return rs.release(state, settings, team.id, player_id, cut.name)
            return replay_ir.activate(state, settings, know, team.id, player_id, drop=cut.player_id)
    return state


def cpu_ir(state: State, settings: Settings, know: Knowledge, seed: int) -> State:
    """Every CPU's IR moves for this week, in waiver-priority order: at most one activation (IR-18) and then
    one placement into an empty slot (IR-19). Never the user's team; a second call changes nothing. `seed`
    is unused (no randomness)."""
    ctx = _context(know, settings)
    for team_id in state.waiver_order:
        if rs.team(state, team_id).is_user:
            continue
        stuck = replay_ir.ineligible(state, know, team_id)
        if stuck:
            state = _bring_back(state, ctx, rs.team(state, team_id), stuck[0])
        team = rs.team(state, team_id)
        pick = None if team.ir else _stash_pick([know.by_id[pid] for pid in team.roster], ctx)
        if pick is not None:
            state = replay_ir.place(state, know, team_id, pick.player_id)
    return state
