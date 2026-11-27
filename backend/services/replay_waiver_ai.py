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
  under a positional limit, add a free agent whose trade value clearly beats a player it can drop at a position
  it can use (`_upgrade`: UPGRADE_SHARE x and UPGRADE_GAP more; to IR at once when he is out), and swap a kicker or defense who does play only for STREAM_EDGE projected points
  (a streamer, about STREAMER_SHARE of CPUs picked once from the game's seed by `streamers`, for
  STREAMER_EDGE: it chases a matchup most weeks).
- A fan never claims or adds his rival's players (replay_fans), unless one is the only way to field his lineup.
- A CPU drops a player it added this week, one it is "high on" (replay_mood) or one at a position it needs
  (replay_styles.position_needs) only after every other player it can spare, and never, to cover a slot, a better
  player who is only on a bye.
- A CPU never drops a top-PROTECTED_RANK player (by HEALTHY value; per 12 teams, scaled by league size), one
  of the league's picks from rounds 1-EARLY_ROUNDS in its first EARLY_WEEKS weeks (unless the news has him out
  for the season), or an injured player who was above replacement or still has trade value (healthy chart
  >= KEEP_OUT_CHART) with a return date in the news (except, to cover a slot nobody plays in while its IR slot is taken, one tagged IR or PUP
  outside the top PROTECTED_RANK), and never into a roster that can't field a lineup. A player the news has out
  for the season is none of these: he is the first it drops, whatever he was worth healthy, and with a full roster
  it cuts him for whoever fills his spot best (`_replace_lost`), unless he sits in its IR slot and no other player
  can take it. No CPU adds one either (`_upgrade`, `_fill_spot`): his chart keeps what a wrong report is worth, and
  a CPU that added him for it would cut him again the week after. For a claim or an
  open spot it drops only one of its DROPPABLE_COUNT least valuable skill players. A kicker or defense is
  only ever swapped for another one.
- No add takes a CPU over its positional limit (`replay_values.crowded`: 2 QB, 2 TE, 1 K, 1 DST, 6 RB,
  7 WR) unless nobody it has there plays this week, and it lets the extra one go again afterwards.
- `cpu_ir` (before claims and free agency): a CPU brings back an IR player who lost his tag (into an open
  spot, swapping in an eligible player worth keeping, or cutting one it can spare, maybe him), then puts its
  most valuable eligible player (by trade value; one expected back before one out for the season) on IR, trading
  places with the slot's player when that one is out for the season or clearly worth less than a player out past
  next week (`_restash`, IR_SWAP_GAP). Size and this week's lineup read the active roster only; season
  points and positional limits also count the IR player (`_Context.stash`): he is coming back.

The only dice roll is `streamers`, once per game; ties go to the better-ranked player, so the same state
gives the same moves.
"""
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from services import (lineup_gain, replay_claim_ai as claim_ai, replay_fans, replay_ir, replay_mood, replay_state as rs,
                      replay_styles, replay_values as rv)
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
UPGRADE_SHARE = 1.5               # a free agent worth this x the trade value of the player he replaces ...
UPGRADE_GAP = 100.0               # ... and this many chart points more (100: about the top 100) is added for him
STREAMED = ("K", "DST")
LONG_TAGS = ("IR", "PUP")         # out for most of what is left (replay_values.IR_OUT_SHARE)
IR_SWAP_GAP = 10.0                # chart points a roster player must be worth more than the IR slot's player to take
                                  # his place (hand-set: no trading places every week between two near-equal players)

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
    fresh: frozenset[int] = frozenset()     # players their team added this week (replay_state.pickups)
    needs: Mapping[int, frozenset[str]] = field(default_factory=dict)   # {team id: positions it needs}
    keep: frozenset[int] = frozenset()      # the team's players it drops only after every other spare one


def _for_team(ctx: _Context, team: rs.Team) -> _Context:
    """`ctx` for one team: its IR player, and the players it drops only after every other one it can spare: one it
    just added, is high on, or holds at a position it needs (testers saw a CPU cut a player it had just claimed,
    one it was "High on", and a QB it needed)."""
    needed = ctx.needs.get(team.id, frozenset())
    keep = frozenset(pid for pid in team.roster if pid in ctx.fresh or ctx.know.by_id[pid].position in needed
                     or ((felt := replay_mood.feeling(ctx.know.by_id[pid], team.persona, team.hype)) is not None
                         and felt.kind == "high"))
    return replace(ctx, stash=tuple(ctx.know.by_id[pid] for pid in team.ir), keep=keep)


def protected_rank(settings: Settings) -> int:
    return PROTECTED_RANK * settings.teams // 12


def _season_over(value: ReplayValue | None) -> bool:
    """Does the news have him out for the season? Never a vague report ("no timetable yet"): one whose guess runs
    past the season also carries no week (replay_news.report), but what the public reads is that nobody knows."""
    return (value is not None and value.out and value.news is not None and value.news.back is None
            and not value.news.vague)


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
    needs = (MappingProxyType({tid: found[0] for tid, found in replay_styles.position_needs(state, settings, know).items()})
             if state is not None else MappingProxyType({}))
    return _Context(know, settings, horizon, rv.projector(know),
                    top | (early_picks(state, settings, know) if state is not None else frozenset()),
                    CLAIM_GAIN_PER_WEEK * sum(horizon.weights.values()),
                    fresh=rs.pickups(state) if state is not None else frozenset(), needs=needs)


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


def _spare(roster: Sequence[ReplayValue], know: Knowledge, protected: frozenset[int],
           keep: frozenset[int] = frozenset()) -> list[ReplayValue]:
    """Every skill player a CPU could bear to lose, least valuable first (by healthy value, so an injury
    alone never makes a good player droppable; nor one out a while who still has trade value). One the news has
    out for the season comes first, whatever he was worth healthy and whoever protects him: nobody holds a
    roster spot for a player who is not coming back (a CPU kept Jalen Hurts on its bench all season)."""
    def safe(value: ReplayValue) -> bool:
        if value.position not in SKILL:
            return False                              # (replacement levels exist for skill positions only)
        if _season_over(value):
            return True
        if value.player_id in protected:
            return False
        return not (value.out and (value.last_projection > know.replacement[value.position]
                                   or (value.healthy_chart >= KEEP_OUT_CHART and _coming_back(value))))

    return sorted((v for v in roster if safe(v)),
                  key=lambda v: (not _season_over(v), v.player_id in keep, v.healthy_value, v.ros_ppg, -v.player_id))


def droppable(roster: Sequence[ReplayValue], know: Knowledge, protected: frozenset[int],
              keep: frozenset[int] = frozenset()) -> list[ReplayValue]:
    """The DROPPABLE_COUNT skill players a CPU is willing to drop for an add, least valuable first (`keep` last)."""
    return _spare(roster, know, protected, keep)[:DROPPABLE_COUNT]


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
    drops = droppable(roster, ctx.know, ctx.protected, ctx.keep) if full else []
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
    spare = _spare(roster, ctx.know, ctx.protected, ctx.keep)
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
                if not out.out and out.projection == 0 and out.healthy_value >= add.healthy_value:
                    continue                          # a bye is no reason to cut a better player
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
    if needs:                                         # one out for the season only when nobody else fills the hole
        return _top([v for v in needs if not _season_over(v)] or needs).player_id, None
    pool = [v for v in candidates(ctx.know, free)
            if not _season_over(v) and not rv.crowded([*roster, *ctx.stash], [v], v.position, ctx.settings)]
    best = _best_add(roster, pool, ctx, MIN_FILL_GAIN)
    add = best[1] if best else next(iter(pool), None)       # nobody helps the lineup: the most valuable
    return (add.player_id, None) if add else None


def _replace_lost(roster: Sequence[ReplayValue], free: Sequence[ReplayValue], ctx: _Context) -> Move | None:
    """A player on the active roster whom the news has out for the season (its IR slot is someone else's) is cut
    for whoever fills his spot best, as an open spot would be filled."""
    for out in _spare(roster, ctx.know, ctx.protected, ctx.keep):
        if not _season_over(out):
            return None                               # they sort first: nobody further down is one
        move = _fill_spot([v for v in roster if v is not out], free, ctx)
        if move is not None and _legal_drops(roster, ctx.know.by_id[move[0]], [out], ctx):
            return move[0], out.player_id
    return None


def _trim(roster: Sequence[ReplayValue], free: Sequence[ReplayValue], ctx: _Context) -> Move | None:
    """Back to the positional limit after a week that needed an extra player: the least valuable one it can
    spare at an over-full position goes (never one a starting slot needs this week) for whoever fills the
    open spot best, as long as that is no step down in value."""
    idle = len(rv.idle_slots(ctx.know, [v.player_id for v in roster], ctx.settings))
    held = Counter(v.position for v in (*roster, *ctx.stash))
    for out in _close_by_chart(_spare(roster, ctx.know, ctx.protected, ctx.keep)):
        left = [v for v in roster if v is not out]
        if (held[out.position] > rv.position_cap(out.position, ctx.settings)
                and len(rv.idle_slots(ctx.know, [v.player_id for v in left], ctx.settings)) <= idle):
            move = _fill_spot(left, free, ctx)
            if move is not None and ctx.know.by_id[move[0]].healthy_value >= out.healthy_value:
                return move[0], out.player_id
    return None


def _close_by_chart(spare: Sequence[ReplayValue]) -> list[ReplayValue]:
    """`spare` (least valuable first) with each run of healthy values within TRIM_CLOSE of its lowest one
    reordered by trade value, lowest first: the model can't tell them apart (often all 0), the market can. The
    players out for the season `_spare` puts in front stay there: they are not in value order."""
    lost = [v for v in spare if _season_over(v)]
    runs: list[list[ReplayValue]] = []
    for value in spare[len(lost):]:
        if runs and value.healthy_value - runs[-1][0].healthy_value <= TRIM_CLOSE:
            runs[-1].append(value)
        else:
            runs.append([value])
    return [*lost, *(v for run in runs for v in sorted(run, key=lambda v: (v.healthy_chart, v.player_id)))]


def _upgrade(roster: Sequence[ReplayValue], free: Sequence[ReplayValue], ctx: _Context) -> Move | None:
    """The free agent with the most trade value who clearly beats (UPGRADE_SHARE x and UPGRADE_GAP more) the least
    valuable player it can drop for him (one of its DROPPABLE_COUNT least valuable, or any it can spare at his
    position) at a position it can use: a manager doesn't leave a 300-point player on
    the wire (testers added and flipped them for 75-85% of chart). He goes to IR after the move when he is out; with the
    IR slot taken an out player is added only when he is worth more than the one on IR (they swap, `_stash_added`), and
    never one who is out but can't go on IR (a suspension) or one the news has out for the season (`_replace_lost`
    would cut him the week after)."""
    spare = _spare(roster, ctx.know, ctx.protected, ctx.keep) if len(roster) >= ctx.settings.roster_size else []
    if not spare:
        return None
    stashed = max((v.chart for v in ctx.stash), default=None)   # IR taken: an out add must beat its player
    for add in sorted((v for v in free if v.position in SKILL), key=lambda v: (-v.chart, v.player_id)):
        if _season_over(add) or (add.out and (not replay_ir.eligible(ctx.know, add.player_id)
                                              or (stashed is not None and add.chart <= stashed))):
            continue                                  # out for the season, or he would sit on the bench (suspended: no IR)
        drops = [*spare[:DROPPABLE_COUNT], *(v for v in spare[DROPPABLE_COUNT:] if v.position == add.position)]
        legal = [out for out in _legal_drops(roster, add, drops, ctx)
                 if add.chart >= max(UPGRADE_SHARE * out.chart, out.chart + UPGRADE_GAP)]
        if legal:
            return add.player_id, min(legal, key=lambda v: (v.chart, v.healthy_value, -v.player_id)).player_id
        if add.chart < UPGRADE_GAP:
            return None                               # sorted: nobody further down can clear the gap
    return None


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
    steps = ((_cover, _fill_spot, _replace_lost, _trim, _upgrade, _stream) if made < MAX_FREE_AGENT_MOVES
             else (_cover,))
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
            state = _stash_added(state, settings, know, team_id, move[0])
    return state


def _stash_added(state: State, settings: Settings, know: Knowledge, team_id: int, added: int) -> State:
    """An out player just added goes to IR at once: into an empty slot, or swapped with a less valuable IR player."""
    if not replay_ir.eligible(know, added):
        return state
    team = rs.team(state, team_id)
    if not team.ir:
        return replay_ir.place(state, know, team_id, added)
    held = team.ir[0]
    if replay_ir.eligible(know, held) and know.by_id[added].chart > know.by_id[held].chart:
        return replay_ir.activate(state, settings, know, team_id, held, swap=added)
    return state


# ---------------- the IR slot ----------------

def _stash_pick(group: Sequence[ReplayValue], ctx: _Context) -> ReplayValue | None:
    """The most valuable (by trade value) eligible skill player, and only one the rest of `group` can field a
    lineup without (a QB-heavy superflex roster may lack a spare back). Any eligible player is better on IR
    than on the bench: it frees his spot (testers saw season-ending injuries on benches beside an empty slot),
    but one the news expects back comes before one it has out for the season, whatever their values."""
    keep = [v for v in group if v.position in SKILL and replay_ir.eligible(ctx.know, v.player_id)
            and rv.can_field_lineup([o.position for o in group if o is not v], ctx.settings)]
    return max(keep, key=lambda v: (not _season_over(v), v.chart, v.healthy_value, -v.player_id), default=None)


def _restash(state: State, ctx: _Context, team: rs.Team) -> State:
    """A taken IR slot goes to the roster player `_stash_pick` would rather have there (chosen with the slot's
    player back on the roster, as he would be): a player the news expects back when the slot's is out for the
    season, else one worth IR_SWAP_GAP chart points more whose news has him out past next week (a one-week absence
    is not worth trading places twice for; "no timetable yet" counts as longer, whatever week it guesses)."""
    held = ctx.know.by_id[team.ir[0]]
    if not replay_ir.eligible(ctx.know, held.player_id):
        return state                                  # he lost his tag and could not come back: still blocked
    pick = _stash_pick([*(ctx.know.by_id[pid] for pid in team.roster), held], ctx)
    if pick is None or pick is held:
        return state
    if not _season_over(held) or _season_over(pick):
        lasting = pick.news is not None and (pick.news.vague or pick.news.back is None
                                             or pick.news.back > ctx.know.week + 1)
        if not lasting or pick.chart < held.chart + IR_SWAP_GAP:
            return state
    return replay_ir.activate(state, ctx.settings, ctx.know, team.id, held.player_id, swap=pick.player_id)


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
    for cut in _spare(group, know, ctx.protected, ctx.keep):
        if rv.can_field_lineup([v.position for v in group if v is not cut], settings):
            if cut.player_id == player_id:
                return rs.release(state, settings, team.id, player_id, cut.name)
            return replay_ir.activate(state, settings, know, team.id, player_id, drop=cut.player_id)
    return state


def cpu_ir(state: State, settings: Settings, know: Knowledge, seed: int) -> State:
    """Every CPU's IR moves for this week, in waiver-priority order: at most one activation (IR-18) and then
    one placement into an empty slot (IR-19) or one swap that gives a taken slot to a player it would rather
    have there (`_restash`). Never the user's team; a second call changes nothing. `seed` is unused (no
    randomness)."""
    ctx = _context(know, settings, state)
    for team_id in state.waiver_order:
        if rs.team(state, team_id).is_user:
            continue
        stuck = replay_ir.ineligible(state, know, team_id)
        if stuck:
            state = _bring_back(state, ctx, rs.team(state, team_id), stuck[0])
        team = rs.team(state, team_id)
        if team.ir:
            state = _restash(state, ctx, team)
            continue
        pick = _stash_pick([know.by_id[pid] for pid in team.roster], ctx)
        if pick is not None:
            state = replay_ir.place(state, know, team_id, pick.player_id)
    return state
