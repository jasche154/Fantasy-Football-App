"""Replay League: how CPU managers use the waiver wire. Everything comes from a `Knowledge` (pre-game
numbers for the current week), so no CPU ever sees a result or a projection from the future.

- `cpu_claims` (before the waiver run): every CPU scores one shared short list of unrostered skill players
  with `lineup_gain` (points added to its best lineup over the rest of the season, after the drop) and
  claims a player worth about a lineup point a week. At most MAX_CLAIMS, the second planned on the roster
  as it would be after the first. Players on a hot streak are valued at their hot rate, join the list and
  need half the bar (services/replay_claim_ai.py); in a FAAB league each claim carries a bid from there.
- `cpu_free_agency` (after the run), free agents only: first cover every starting slot (kicker and
  defense included) that nobody on the roster can fill with a projection this week (bye or out), whatever
  that costs later. Then, up to MAX_FREE_AGENT_MOVES moves in all: fill an empty roster spot, get back
  under a positional limit, and swap a kicker or defense who does play only for STREAM_EDGE projected points
  (a streamer, about STREAMER_SHARE of CPUs picked once from the game's seed by `streamers`, for
  STREAMER_EDGE: it chases a matchup most weeks).
- A fan never claims or adds his rival's players (replay_fans), unless one is the only way to field his lineup.
- A CPU never drops a top-PROTECTED_RANK player (by HEALTHY value; per 12 teams, scaled by league size), one
  of the league's picks from rounds 1-EARLY_ROUNDS in its first EARLY_WEEKS weeks (unless the news has him out
  for the season), or an injured player who was above replacement or still has trade value (healthy chart
  >= KEEP_OUT_CHART) with a return date in the news (except, to cover a slot nobody plays in while its IR slot is taken, one tagged IR or PUP
  outside the top PROTECTED_RANK), and never into a roster that can't field a lineup. For a claim or an
  open spot it drops only one of its DROPPABLE_COUNT least valuable skill players. A kicker or defense is
  only ever swapped for another one.
- No add takes a CPU over its positional limit (`replay_values.crowded`: 2 QB, 2 TE, 1 K, 1 DST, 6 RB,
  7 WR) unless nobody it has there plays this week, and it lets the extra one go again afterwards.
- `cpu_ir` (before claims and free agency): a CPU brings back an IR player who lost his tag (into an open
  spot, swapping in an eligible player worth keeping, or cutting one it can spare, maybe him), then puts its
  best eligible player worth keeping on IR. Size and this week's lineup read the active roster only; season
  points and positional limits also count the IR player (`_Context.stash`): he is coming back.

The only dice roll is `streamers`, once per game; ties go to the better-ranked player, so the same state
gives the same moves.
"""
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from services import (lineup_gain, replay_claim_ai as claim_ai, replay_fans, replay_ir, replay_state as rs,
                      replay_values as rv)
from services import replay_waivers
from services.replay_cover import cpu_cover  # noqa: F401  (re-exported: replay_game and tests call it here)
from services.replay_pool import SKILL
from services.replay_state import Claim, Settings, State
from services.replay_values import Knowledge, ReplayValue

MAX_BY_VALUE = 16                 # shared candidates: the best unrostered skill players by trade value
MAX_BY_PROJECTION = 2             # plus this many per skill position by this week's projection (8 in all)
CLAIM_GAIN_PER_WEEK = 1.0         # claim when the gain is this many lineup points per (weighted) week left
MAX_CLAIMS = 2                    # per CPU per waiver run
MAX_FREE_AGENT_MOVES = 2          # per CPU per week (covering a starting slot is always allowed)
DROPPABLE_COUNT = 4               # a CPU only drops one of its lowest skill players by healthy value
PROTECTED_RANK = 100              # never a player in the league's top 100 by healthy value (12 teams; scaled
                                  # by league size, else nearly every rostered player of an 8-team league is safe)
EARLY_ROUNDS = 8                  # draft picks from these rounds (pick 96 of 12 teams: the depth PROTECTED_RANK
EARLY_WEEKS = 4                   # covers) stay through the league's first 4 weeks: about three games before a
                                  # manager gives up on a mid-round pick (2026: a CPU cut its round-7 TE after one)
KEEP_OUT_CHART = 50.0             # an out player with this healthy chart (about the top 125) is kept while he is
                                  # expected back: a manager stashes him (2026: a CPU cut Jordyn Tyson, 63, a
                                  # week before his return; he was worth 246 three weeks later)
TRIM_CLOSE = 5.0                  # healthy values this close (season points over replacement) are a tie in
                                  # `_trim`, which then keeps the higher trade value
MIN_FILL_GAIN = 0.0               # a player for an open roster spot must add more than this over the season
STREAM_EDGE = 4.0                 # projected points a free-agent K / DST must beat the team's own by (real
                                  # seasons: a league's CPUs make about 56 such swaps a season at 2.0, nearly
                                  # all of them defenses chasing a matchup, 28 at 3.0 and 12 at 4.0)
STREAMER_EDGE = 1.5               # a streamer's edge: it plays the matchup at K and DST
STREAMER_SHARE = 0.4              # about this share of CPU managers stream
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
    hot: frozenset[int] = frozenset()       # claims only: hot players, who need just `hot_gain`
    hot_gain: float = 0.0
    stream_edge: float = STREAM_EDGE        # free agency only: this team's K / DST swap edge


def _for_team(ctx: _Context, team: rs.Team) -> _Context:
    return replace(ctx, stash=tuple(ctx.know.by_id[pid] for pid in team.ir))


def protected_rank(settings: Settings) -> int:
    return PROTECTED_RANK * settings.teams // 12


def _season_over(value: ReplayValue | None) -> bool:
    """Does the news have him out for the season?"""
    return value is not None and value.out and value.news is not None and value.news.back is None


def _coming_back(value: ReplayValue) -> bool:
    """Does the news name a week he is expected back?"""
    return value.news is not None and value.news.back is not None


def early_picks(state: State, settings: Settings, know: Knowledge) -> frozenset[int]:
    """The league's picks from rounds 1-EARLY_ROUNDS while it is in its first EARLY_WEEKS weeks."""
    if state.week >= settings.start_week + EARLY_WEEKS:
        return frozenset()
    return frozenset(pick.player_id for pick in state.picks
                     if pick.round <= EARLY_ROUNDS and not _season_over(know.by_id.get(pick.player_id)))


def _context(know: Knowledge, settings: Settings, state: State | None = None) -> _Context:
    horizon = rv.horizon(know, settings)
    valued = sorted((v for v in know.values if v.healthy_value > 0), key=lambda v: (-v.healthy_value, v.player_id))
    top = frozenset(v.player_id for v in valued[:protected_rank(settings)])
    return _Context(know, settings, horizon, rv.projector(know),
                    top | (early_picks(state, settings, know) if state is not None else frozenset()),
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
    alone never makes a good player droppable; nor one out a while who still has trade value)."""
    def safe(value: ReplayValue) -> bool:
        if value.position not in SKILL or value.player_id in protected:
            return False                              # (replacement levels exist for skill positions only)
        return not (value.out and (value.last_projection > know.replacement[value.position]
                                   or (value.healthy_chart >= KEEP_OUT_CHART and _coming_back(value))))

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
    slots = {"QB": starters.get("QB", 0) + starters.get("SUPERFLEX", 0),     # a superflex usually starts a QB
             "FLEX": sum(starters.get(slot, 0) for slot in ("FLEX", *rv.FLEX_POSITIONS))}
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
    rate = ctx.projector.healthy_rate                           # a hot player's hot rate
    weakest = min((rate(v) for v in drops), default=0.0)
    best = None
    ceilings = sorted(((_ceiling(add, bars, ctx), order, add) for order, add in enumerate(pool)),
                      key=lambda row: (-row[0], row[1]))
    lowest = min(needed, ctx.hot_gain) if ctx.hot else needed
    for ceiling, _, add in ceilings:
        if ceiling <= (best[0] if best else lowest):
            break                                     # nobody further down the list can do better
        needed_here = ctx.hot_gain if add.player_id in ctx.hot else needed
        if full and max(rate(add), add.projection) <= weakest:
            continue
        if not full and rv.crowded(owned, [add], add.position, ctx.settings):
            continue
        # Without a drop the gain can only be higher, so this one sum rules out most of the rest.
        gain = round(lineup_gain.season_points([*owned, add], ctx.horizon, ctx.projector, league) - base, 2)
        if gain <= needed_here:
            continue
        out = None
        if full:
            legal = _legal_drops(roster, add, drops, ctx)
            if not legal:
                continue
            gain, out = lineup_gain.best_add(owned, legal, add, ctx.horizon, ctx.projector, league)
        if gain > needed_here and (best is None or gain > best[0]):
            best = (gain, add, out)
    return best


def _plan_claims(roster: Sequence[ReplayValue], pool: Sequence[ReplayValue],
                 ctx: _Context) -> list[tuple[int, int | None, float]]:
    """Up to MAX_CLAIMS claims for one roster as (add, drop, gain), best first; each is scored on the roster
    as it would be after the ones before it."""
    moves: list[tuple[int, int | None, float]] = []
    roster, pool = list(roster), list(pool)
    while len(moves) < MAX_CLAIMS:
        best = _best_add(roster, pool, ctx, ctx.claim_gain)
        if best is None:
            break
        gain, add, out = best
        moves.append((add.player_id, out.player_id if out else None, gain))
        roster = [v for v in roster if v is not out] + [add]
        pool = [v for v in pool if v is not add]
    return moves


def cpu_claims(state: State, settings: Settings, know: Knowledge, seed: int) -> dict[int, list[Claim]]:
    """Every CPU's waiver claims for this week's run, best first ({team id: claims}; teams with none are
    left out). Hand the result to `replay_waivers.process_claims`. Claim ids start at `state.next_id` and are
    never stored. Hot players count at their hot rate (a fresh `HotProjector`, never the shared one) and
    need `claim_ai.HOT_CLAIM_GAIN_PER_WEEK`; `seed` drives the FAAB bids (0 outside FAAB)."""
    base = _context(know, settings, state)
    weights = sum(base.horizon.weights.values())
    rates = claim_ai.hot_rates(know)
    ctx = replace(base, projector=claim_ai.HotProjector(know, rates), hot=frozenset(rates),
                  hot_gain=claim_ai.HOT_CLAIM_GAIN_PER_WEEK * weights)
    free = _unrostered(state, know)
    listed = candidates(know, free)
    pool = listed + claim_ai.hot_candidates(know, free, listed)
    claims: dict[int, list[Claim]] = {}
    next_id = state.next_id
    for team in state.teams:
        if team.is_user:
            continue
        mine = [v for v in pool if not replay_fans.banned(team, v.team)]
        moves = _plan_claims([know.by_id[pid] for pid in team.roster], mine, _for_team(ctx, team))
        if moves:
            claims[team.id] = _priced(state, settings, seed, team.id, moves, weights, next_id)
            next_id += len(moves)
    return claims


def _priced(state: State, settings: Settings, seed: int, team_id: int, moves: Sequence[tuple[int, int | None, float]],
            weights: float, first_id: int) -> list[Claim]:
    """The team's claims with their bids. A later claim was planned on the roster after the earlier ones, so it
    never outbids them (the highest bid runs first) and the bids together fit the budget left."""
    claims, cap, left = [], None, rs.faab_left(state, team_id)
    for n, (add, out, gain) in enumerate(moves):
        bid = claim_ai.claim_bid(state, settings, seed, team_id, add, gain, weights)
        bid = min(bid, left) if cap is None else min(bid, cap, left)
        claims.append(Claim(first_id + n, team_id, add, out, bid))
        cap, left = bid, left - bid
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
        eligible = rv.eligible(slot)
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
    for out in _close_by_chart(_spare(roster, ctx.know, ctx.protected)):
        left = [v for v in roster if v is not out]
        if (held[out.position] > rv.position_cap(out.position, ctx.settings)
                and len(rv.idle_slots(ctx.know, [v.player_id for v in left], ctx.settings)) <= idle):
            move = _fill_spot(left, free, ctx)
            if move is not None and ctx.know.by_id[move[0]].healthy_value >= out.healthy_value:
                return move[0], out.player_id
    return None


def _close_by_chart(spare: Sequence[ReplayValue]) -> list[ReplayValue]:
    """`spare` (least valuable first) with each run of healthy values within TRIM_CLOSE of its lowest one
    reordered by trade value, lowest first: the model can't tell them apart (often all 0), the market can."""
    runs: list[list[ReplayValue]] = []
    for value in spare:
        if runs and value.healthy_value - runs[-1][0].healthy_value <= TRIM_CLOSE:
            runs[-1].append(value)
        else:
            runs.append([value])
    return [v for run in runs for v in sorted(run, key=lambda v: (v.healthy_chart, v.player_id))]


def _stream(roster: Sequence[ReplayValue], free: Sequence[ReplayValue], ctx: _Context) -> Move | None:
    """Swap a kicker for a kicker or a defense for a defense when the free agent beats the team's own by
    `ctx.stream_edge` this week (one who does not play at all is `_cover`'s job). The bigger edge wins."""
    best: tuple[float, Move] | None = None
    for position in STREAMED:
        add = _top(v for v in free if v.position == position and v.projection > 0)
        own = _worst_own(roster, add) if add else None
        if own is None:
            continue
        edge = add.projection - _top(v for v in roster if v.position == position).projection
        if edge >= ctx.stream_edge and (best is None or edge > best[0]):
            best = (edge, (add.player_id, own.player_id))
    return best[1] if best else None


def streamers(seed: int, cpu_ids: Sequence[int]) -> frozenset[int]:
    """The CPUs that stream K and DST: each one with chance STREAMER_SHARE, fixed for the whole game."""
    return frozenset(team_id for team_id in cpu_ids if rs.rng(seed, "streamer", team_id).random() < STREAMER_SHARE)


def _next_move(state: State, team_id: int, ctx: _Context, made: int) -> Move | None:
    team = rs.team(state, team_id)
    roster, ctx = [ctx.know.by_id[pid] for pid in team.roster], _for_team(ctx, team)
    free = _unrostered(state, ctx.know, state.locked)
    allowed = [v for v in free if not replay_fans.banned(team, v.team)]
    steps = (_cover, _fill_spot, _trim, _stream) if made < MAX_FREE_AGENT_MOVES else (_cover,)
    found = next((move for step in steps if (move := step(roster, allowed, ctx)) is not None), None)
    return found if found is not None or len(allowed) == len(free) else _cover(roster, free, ctx)


def cpu_free_agency(state: State, settings: Settings, know: Knowledge, seed: int) -> State:
    """Every CPU's free-agent moves for this week, in waiver-priority order (which they do not use up):
    never a player in `state.locked`, and at most MAX_FREE_AGENT_MOVES each unless a starting slot still
    has nobody who plays. Works in any phase; the dropped players go on waivers through
    `replay_state.release`. `seed` (the game's) only picks the `streamers`."""
    base = _context(know, settings, state)
    cpus = [team_id for team_id in state.waiver_order if not rs.team(state, team_id).is_user]
    streaming = streamers(seed, sorted(cpus))
    for team_id in cpus:
        if replay_ir.blocked_reason(state, know, team_id):            # execute_move would refuse every add
            continue
        ctx = replace(base, stream_edge=STREAMER_EDGE if team_id in streaming else STREAM_EDGE)
        for made in range(sum(settings.starters.values())):           # one per starting slot at the very most
            move = _next_move(state, team_id, ctx, made)
            if move is None:
                break
            state = replay_waivers.execute_move(state, settings, know, team_id, *move)
    return state


# ---------------- the IR slot ----------------

def _stash_pick(group: Sequence[ReplayValue], ctx: _Context) -> ReplayValue | None:
    """The best eligible skill player the team would never spare (top player, or above replacement), and only
    one the rest of `group` can field a lineup without (a QB-heavy superflex roster may lack a spare back)."""
    keep = [v for v in group if v.position in SKILL and replay_ir.eligible(ctx.know, v.player_id)
            and (v.player_id in ctx.protected or v.last_projection > ctx.know.replacement[v.position])
            and rv.can_field_lineup([o.position for o in group if o is not v], ctx.settings)]
    return max(keep, key=lambda v: (v.healthy_value, v.ros_ppg, -v.player_id), default=None)


def _bring_back(state: State, ctx: _Context, team: rs.Team, player_id: int) -> State:
    """IR-18: an IR player who lost his tag comes back into an open spot, swapped for an eligible player
    worth keeping, or for the least valuable player it can spare (maybe himself). Else it stays blocked."""
    settings, know = ctx.settings, ctx.know
    if len(team.roster) < settings.roster_size:
        return replay_ir.activate(state, settings, know, team.id, player_id)
    group = [*(know.by_id[pid] for pid in team.roster), know.by_id[player_id]]
    pick = _stash_pick(group, ctx)                  # never him: he lost his tag
    if pick is not None:
        return replay_ir.activate(state, settings, know, team.id, player_id, swap=pick.player_id)
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
    ctx = _context(know, settings, state)
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
