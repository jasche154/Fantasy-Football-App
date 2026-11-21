"""Replay League trades: the league's rules, a CPU manager's acceptance test, and the user's proposals.

`evaluate` is deterministic: a ValueError for input that cannot be a trade, else a `Verdict` whose decline
lists its reasons. Every CPU team in the deal must pass both of these, on point-in-time values only (`know`):

1. Value. `replay_trade_value.tally` on its persona's private values scores >= 0: what it gets (plus the
   best-player premium, a freed roster spot and half its lineup gain) against what it gives x a multiple:
   1.00 (normal) or 1.08 (hard) against the user, 0.95 against another CPU.
2. Never stupid. On neutral chart values it gets back at least MARKET_GUARD of what it gives
   (`replay_trade_value.market_ok`), whatever its persona.
3. One accepted trade per CPU per week; a CPU blocked by its IR (`replay_ir.blocked_reason`) declines all.

And for both teams: at most MAX_PLAYERS_PER_SIDE players a side, nobody traded in the last two weeks, a roster
within the limit (the user names who to drop; a CPU cuts its lowest healthy-chart skill players, never its
only kicker or defense), and the one position rule: every starting slot the deal leaves empty
(`replay_values.missing_slots`) has its own eligible free agent to fill it (unrostered, not on waivers, not
out). Positions need not match: a CPU sends its only tight end and picks one up (`replay_waiver_ai.cpu_cover`).
Every drop goes through `replay_state.release`. An out player on the active roster may be traded; one in an
IR slot can't be, but still counts for a team's lineup gain.

The user is always the proposer in `evaluate`: to look at a CPU's offer, mirror it (`respond` does).
"""
import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from functools import lru_cache

from services import lineup_gain, replay_ir, replay_state as rs, replay_trade_value as tv, replay_values as rv, trade
from services.replay_pool import SKILL
from services.replay_state import Offer, Settings, State, Team
from services.replay_values import Knowledge

log = logging.getLogger(__name__)

TRADE_PHASES = ("waivers", "open")
MAX_PLAYERS_PER_SIDE = 3
CPU_TRADE_MULTIPLE = 0.95       # between two CPUs: each needs back at least this share of what it gives
SUGGEST_WITHIN = 1.5            # a player suggested to close a value gap is worth at most this x what is missing
VALUE_REASON = "Value is too far apart"     # every value decline starts so (a counter-offer answers only those)
FLEX_ELIGIBLE = ("RB", "WR", "TE")


@dataclass(frozen=True)
class Verdict:
    """The answer to "from_team sends `give` and receives `get`". "Your" is from_team, "their" is to_team."""
    accepted: bool
    reasons: tuple[str, ...]                # why not, as plain sentences; empty when accepted
    value_give: float                       # chart value from_team sends (plus the premium if he sends the best)
    value_get: float                        # chart value from_team keeps, minus its drops (plus the premium)
    percent: float                          # (get - give) / the larger: above 0, from_team gets more
    their_gain: float                       # season-weighted lineup points, after drops and free-agent cover
    your_gain: float
    their_drops: tuple[int, ...]            # who to_team (a CPU) would cut to stay within the roster limit
    fair_label: str                         # trade.verdict_for, for from_team: "fair", "slight_win", "big_loss"...
    your_drops: tuple[int, ...] = ()
    give_values: tuple[tuple[int, float], ...] = ()     # (player_id, chart), in the order given
    get_values: tuple[tuple[int, float], ...] = ()
    suggestion: str | None = None           # a hint that would fix a value decline
    score: float = 0.0                      # the lowest CPU net in the deal on the 1000 chart scale (to_team's
                                            # when the user proposes); accepted needs >= 0


class OfferExpired(ValueError):
    """`respond(accept=True)` on an offer that no longer stands. `state` has it marked "expired": save it."""

    def __init__(self, message: str, state: State, verdict: Verdict | None = None):
        super().__init__(message)
        self.state, self.verdict = state, verdict


@dataclass(frozen=True)
class _Side:
    """One team's half of a deal."""
    team: Team
    sends: tuple[int, ...]
    receives: tuple[int, ...]
    drops: tuple[int, ...] | None           # None: over the roster limit with nobody it could cut
    roster: tuple[int, ...]                 # after the deal and the drops
    kept: tuple[int, ...]                   # what it receives and keeps
    cover: tuple[int, ...] | None           # free agents for its empty starting slots; None: nobody eligible
    gain: float
    tally: tv.Tally | None = None           # CPU sides only
    market_ok: bool = True


def needed_multiple(settings: Settings, other: Team) -> float:
    """The share of what it gives that a CPU needs back when dealing with `other`."""
    return rs.USER_TRADE_MULTIPLE[settings.difficulty] if other.is_user else CPU_TRADE_MULTIPLE


def fair_label(know: Knowledge, value_give: float, value_get: float) -> str:
    """trade.verdict_for on chart values for the team that sends `value_give`: "fair", "slight_win"..."""
    return trade.verdict_for(trade.percent_difference(value_give, value_get))


def _names(know: Knowledge, player_ids: Sequence[int]) -> str:
    return ", ".join(know.by_id[pid].name for pid in player_ids)


def _positions(know: Knowledge, player_ids: Sequence[int]) -> list[str]:
    return [know.by_id[pid].position for pid in player_ids]


def _chart(know: Knowledge, player_ids: Sequence[int]) -> float:
    return sum(know.by_id[pid].chart for pid in player_ids)


def _check(state: State, settings: Settings, know: Knowledge, from_team: int, to_team: int,
           give: tuple[int, ...], get: tuple[int, ...]) -> tuple[Team, Team]:
    """(sender, receiver), or ValueError when this cannot be a trade at all."""
    if state.phase not in TRADE_PHASES:
        raise ValueError("Trades are open from the end of the draft to the end of the season.")
    if not rs.trades_open(state, settings):
        raise ValueError(f"The trade deadline has passed: no trades after week {settings.trade_deadline_week}.")
    if know.week != state.week:
        raise ValueError(f"These player values are for week {know.week}, but the league is in week {state.week}.")
    sender, receiver = rs.team(state, from_team), rs.team(state, to_team)
    if sender.id == receiver.id:
        raise ValueError("A team can't trade with itself.")
    if receiver.is_user:
        raise ValueError("Look at a deal with your team from your side: you send `give` and receive `get`.")
    if not give or not get:
        raise ValueError("Both sides of a trade need at least one player.")
    if max(len(give), len(get)) > MAX_PLAYERS_PER_SIDE:
        raise ValueError(f"A trade can include at most {MAX_PLAYERS_PER_SIDE} players on each side.")
    if len({*give, *get}) != len(give) + len(get):
        raise ValueError("A player is listed more than once in the trade.")
    hurt = [pid for owner, ids in ((sender, give), (receiver, get)) for pid in ids if pid in owner.ir]
    if hurt:
        raise ValueError(f"On IR, so not tradable until activated: {_names(know, hurt)}.")
    for owner, ids in ((sender, give), (receiver, get)):
        missing = [pid for pid in ids if pid not in owner.roster]
        if missing:
            known = [know.by_id[pid].name if pid in know.by_id else f"Player {pid}" for pid in missing]
            raise ValueError(f"Not on {owner.name}'s roster: {', '.join(known)}.")
    return sender, receiver


def _user_drops(team: Team, settings: Settings, kept: Sequence[int], drops: tuple[int, ...]) -> tuple[int, ...]:
    """The user's own choice of who to cut, which must cover the overflow exactly."""
    over = max(0, len(kept) - settings.roster_size)
    if len(set(drops)) != len(drops) or any(pid not in team.roster or pid not in kept for pid in drops):
        raise ValueError("You can only drop players who are on your roster and not part of the trade.")
    if len(drops) != over:
        raise ValueError(f"This trade leaves you {over} over the roster limit of {settings.roster_size}: "
                         f"choose exactly {over} to drop." if over else "This trade doesn't need you to drop anyone.")
    return drops


def _cpu_drops(settings: Settings, know: Knowledge, kept: Sequence[int]) -> tuple[int, ...] | None:
    """Who a CPU cuts to get back to the roster limit: its lowest healthy-chart skill players that leave the
    lineup legal. Never a kicker or a defense. None when nobody can go."""
    roster, drops = list(kept), []
    while len(roster) > settings.roster_size:
        legal = rv.can_field_lineup(_positions(know, roster), settings)
        skill = sorted((know.by_id[pid] for pid in roster if know.by_id[pid].position in SKILL),
                       key=lambda v: (v.healthy_chart, v.healthy_value, v.ros_ppg, v.player_id))
        cut = next((v.player_id for v in skill if not legal or rv.can_field_lineup(
            _positions(know, [pid for pid in roster if pid != v.player_id]), settings)), None)
        if cut is None:
            return None
        roster.remove(cut)
        drops.append(cut)
    return tuple(drops)


def free_agents(state: State, know: Knowledge) -> list[int]:
    """Who a team could pick up right now to fill a slot: unrostered, not on waivers, not out; best first."""
    taken = rs.rostered(state) | set(state.locked)
    pool = (v for v in know.values if v.player_id not in taken and not v.out)
    return [v.player_id for v in sorted(pool, key=lambda v: (-v.chart, -v.ros_ppg, v.player_id))]


def cover(know: Knowledge, settings: Settings, roster: Sequence[int], pool: Sequence[int],
          used: frozenset[int] = frozenset()) -> tuple[int, ...] | None:
    """One free agent from `pool` (best first) per starting slot `roster` cannot fill, none in `used`; None
    when a slot has nobody eligible. () for a legal roster."""
    picked: list[int] = []
    for slot in rv.missing_slots(_positions(know, roster), settings):
        eligible = FLEX_ELIGIBLE if slot == "FLEX" else (slot,)
        found = next((pid for pid in pool if pid not in used and pid not in picked
                      and know.by_id[pid].position in eligible), None)
        if found is None:
            return None
        picked.append(found)
    return tuple(picked)


@lru_cache(maxsize=1024)
def _season(settings: Settings, know: Knowledge, roster: tuple[int, ...]) -> float:
    """Season-weighted points of the roster's best skill lineups. Cached: a search asks about the same
    rosters again and again (a Knowledge is immutable and hashed by identity, so entries never go stale)."""
    skill = [know.by_id[pid] for pid in roster if know.by_id[pid].position in SKILL]
    return lineup_gain.season_points(skill, rv.horizon(know, settings), rv.projector(know), know.league)


def clear_cache() -> None:
    _season.cache_clear()


def _side(state: State, settings: Settings, know: Knowledge, team: Team, other: Team, sends: tuple[int, ...],
          receives: tuple[int, ...], drops: tuple[int, ...], pool: Sequence[int],
          used: frozenset[int] = frozenset()) -> _Side:
    after = (*(pid for pid in team.roster if pid not in sends), *receives)
    if team.is_user:
        cut = _user_drops(team, settings, after, drops)
    elif drops:
        raise ValueError("A CPU team chooses its own drops.")
    else:
        cut = _cpu_drops(settings, know, after)
    roster = tuple(pid for pid in after if pid not in (cut or ()))
    filled = cover(know, settings, roster, pool, used)
    gain = round(_season(settings, know, (*roster, *(filled or ()), *team.ir))
                 - _season(settings, know, rs.held(team)), 2)
    kept = tuple(pid for pid in receives if pid in roster)     # a throw-in it cuts at once is worth nothing
    side = _Side(team, sends, receives, cut, roster, kept, filled, gain)
    if team.is_user:
        return side
    own_cuts = tuple(pid for pid in (cut or ()) if pid not in receives)
    per_week = gain / max(1e-9, sum(rv.horizon(know, settings).weights.values()))
    counted = tv.tally(know, team.persona, sends, kept, own_cuts, per_week, needed_multiple(settings, other),
                       other.is_user)
    return replace(side, tally=counted, market_ok=tv.market_ok(know, sends, kept, own_cuts))


def _rule_reasons(state: State, know: Knowledge, sender: Team, receiver: Team, moved: tuple[int, ...]) -> list[str]:
    reasons = []
    locked = [pid for pid in moved if rs.retrade_locked(state, pid)]
    if locked:
        reasons.append(f"Traded in the last {rs.RETRADE_LOCK_WEEKS} weeks, so not yet tradable again: "
                       f"{_names(know, locked)}.")
    reasons.extend(f"{team.name} has already made a trade this week." for team in (sender, receiver)
                   if not team.is_user and rs.has_traded_this_week(state, team.id))
    reasons.extend(reason for team in (sender, receiver)
                   if not team.is_user and (reason := replay_ir.blocked_reason(state, know, team.id)))
    return reasons


def _value_reason(side: _Side, other: Team, settings: Settings, know: Knowledge) -> str | None:
    counted, name = side.tally, side.team.name
    if counted is not None and counted.score < 0:
        multiple = needed_multiple(settings, other)
        extra = f" They want {multiple - 1:.0%} more than they give." if multiple > 1 else ""
        cut = [pid for pid in side.receives if pid not in side.kept]
        if cut:
            extra += f" They would drop {_names(know, cut)} to make room, so that counts for nothing."
        held = [pid for pid in side.sends if other.is_user and know.by_id[pid].out]
        if held:
            extra += (f" They won't let {_names(know, held)} go for less than {tv.OUT_SELL_SHARE:.0%} of "
                      f"healthy value while out.")
        return (f"{VALUE_REASON}: {name} would give {counted.sent + max(0.0, -counted.premium):.0f} of value and "
                f"get {counted.received + max(0.0, counted.premium):.0f} back.{extra}")
    if not side.market_ok:
        return f"{VALUE_REASON}: {name} won't take back less than {tv.MARKET_GUARD:.0%} of what it gives."
    return None


def _side_reasons(side: _Side, other: Team, settings: Settings, know: Knowledge) -> list[str]:
    name, reasons = side.team.name, []
    if side.drops is None:
        reasons.append(f"{name} would be over the roster limit with nobody it could drop.")
    if side.cover is None:
        short = ", ".join(rv.missing_slots(_positions(know, side.roster), settings))
        reasons.append(f"It would leave {name} without a full starting lineup (short at {short}) and no free "
                       f"agent to fill it.")
    value = _value_reason(side, other, settings, know)
    return [*reasons, *([value] if value else [])]


def _points_text(points: float) -> str:
    whole = max(1, round(points))
    return f"Add about {whole} point{'' if whole == 1 else 's'} of value to make this fair."


def _suggestion(state: State, settings: Settings, know: Knowledge, mine: _Side, theirs: _Side) -> str | None:
    """How the user could fix a value decline by to_team: add the one of his spare skill players worth
    closest to what is missing (at most SUGGEST_WITHIN x it, never one they would cut), else that many points."""
    counted = theirs.tally
    if counted is None or not mine.team.is_user:
        return None
    sent = _chart(know, theirs.sends)
    missing = max(-counted.score, tv.MARKET_GUARD * sent - _chart(know, theirs.kept))
    if missing <= 0:
        return None
    if len(mine.sends) >= MAX_PLAYERS_PER_SIDE:
        return _points_text(missing)
    spare = [know.by_id[pid] for pid in mine.team.roster if pid not in mine.sends and pid not in (mine.drops or ())
             and not rs.retrade_locked(state, pid)]
    enough = [v for v in spare if v.position in SKILL and not v.out and missing <= v.chart <= SUGGEST_WITHIN * missing]
    if enough:
        closest = min(enough, key=lambda v: (v.chart - missing, v.player_id))
        kept = (*(pid for pid in theirs.team.roster if pid not in theirs.sends), *theirs.receives, closest.player_id)
        if closest.player_id not in (_cpu_drops(settings, know, kept) or ()):      # never one they would cut
            return f"Add {closest.name} to make this fair."
    return _points_text(missing)


def evaluate(state: State, settings: Settings, know: Knowledge, from_team: int, to_team: int,
             give: Sequence[int], get: Sequence[int], proposer_drops: Sequence[int] = ()) -> Verdict:
    """Would this deal go through? from_team sends `give` and receives `get`; to_team is a CPU.

    ValueError for input that cannot be a trade (wrong roster, too many players, past the deadline, the
    user's drops not covering the overflow). Otherwise a Verdict, the same one every time it is asked."""
    give, get = tuple(give), tuple(get)
    sender, receiver = _check(state, settings, know, from_team, to_team, give, get)
    pool = free_agents(state, know)
    mine = _side(state, settings, know, sender, receiver, give, get, tuple(proposer_drops), pool)
    theirs = _side(state, settings, know, receiver, sender, get, give, (), pool, frozenset(mine.cover or ()))
    reasons = [*_rule_reasons(state, know, sender, receiver, (*give, *get)),
               *_side_reasons(theirs, sender, settings, know), *_side_reasons(mine, receiver, settings, know)]
    bonus = tv.premium(know, "balanced", give, mine.kept)
    value_give = round(_chart(know, give) + max(0.0, -bonus), 2)
    value_get = round(_chart(know, mine.kept) - _chart(know, mine.drops or ()) + max(0.0, bonus), 2)
    scores = [side.tally.score for side in (mine, theirs) if side.tally is not None]
    return Verdict(
        accepted=not reasons, reasons=tuple(reasons), value_give=value_give, value_get=value_get,
        percent=trade.percent_difference(value_give, value_get), their_gain=theirs.gain, your_gain=mine.gain,
        their_drops=theirs.drops or (), fair_label=fair_label(know, value_give, value_get),
        your_drops=mine.drops or (),
        give_values=tuple((pid, know.by_id[pid].chart) for pid in give),
        get_values=tuple((pid, know.by_id[pid].chart) for pid in get),
        suggestion=_suggestion(state, settings, know, mine, theirs), score=min(scores, default=0.0))


def record_offer(state: State, from_team: int, to_team: int, give: Sequence[int], get: Sequence[int],
                 status: str, counter_to: int | None = None) -> State:
    """The state with a new Offer for this week (`give` is what from_team sends)."""
    offer = Offer(id=state.next_id, week=state.week, from_team=from_team, to_team=to_team, give=tuple(give),
                  get=tuple(get), status=status, counter_to=counter_to)
    return replace(state, offers=(*state.offers, offer), next_id=state.next_id + 1)


def _with_status(state: State, offer_id: int, status: str) -> State:
    return replace(state, offers=tuple(replace(o, status=status) if o.id == offer_id else o for o in state.offers))


def _apply(state: State, settings: Settings, know: Knowledge, from_team: int, to_team: int, give: tuple[int, ...],
           get: tuple[int, ...], verdict: Verdict, offer_id: int | None = None, counter_to: int | None = None) -> State:
    """An accepted deal carried out: rosters swapped, re-trade lock set, Offer "accepted", event, forced drops."""
    sender, receiver = rs.team(state, from_team), rs.team(state, to_team)
    state = rs.with_team(state, replace(sender, roster=(*(p for p in sender.roster if p not in give), *get)))
    state = rs.with_team(state, replace(receiver, roster=(*(p for p in receiver.roster if p not in get), *give)))
    state = replace(state, traded={**state.traded, **dict.fromkeys((*give, *get), state.week)})
    state = (record_offer(state, from_team, to_team, give, get, "accepted", counter_to) if offer_id is None
             else _with_status(state, offer_id, "accepted"))
    text = f"{sender.name} traded {_names(know, give)} to {receiver.name} for {_names(know, get)}"
    state = rs.add_event(state, "trade", from_team, text, (*give, *get))
    for team_id, drops in ((from_team, verdict.your_drops), (to_team, verdict.their_drops)):
        for pid in drops:
            state = rs.release(state, settings, team_id, pid, know.by_id[pid].name)
    log.info("Replay trade in week %s: team %s and team %s", state.week, from_team, to_team)
    return state


def execute(state: State, settings: Settings, know: Knowledge, from_team: int, to_team: int,
            give: Sequence[int], get: Sequence[int], proposer_drops: Sequence[int] = ()) -> State:
    """Internal (CPU-CPU deals): carries the trade out. ValueError unless `evaluate` accepts it."""
    verdict = evaluate(state, settings, know, from_team, to_team, give, get, proposer_drops)
    if not verdict.accepted:
        raise ValueError(f"That trade was turned down. {' '.join(verdict.reasons)}")
    return _apply(state, settings, know, from_team, to_team, tuple(give), tuple(get), verdict)


def _countered(state: State, to_team: int, counter_to: int) -> State:
    """The CPU offer the user answers with a counter, marked "countered". ValueError when it can't be."""
    user = rs.user_team(state)
    offer = next((o for o in state.offers if o.id == counter_to), None)
    if offer is None or offer.to_team != user.id or offer.from_team != to_team:
        raise ValueError("There is no such offer from that team to counter.")
    if offer.status != "pending" or offer.week != state.week:
        raise ValueError(f"That offer is already {offer.status}." if offer.status != "pending"
                         else f"That offer was made in week {offer.week}.")
    return _with_status(state, counter_to, "countered")


def propose(state: State, settings: Settings, know: Knowledge, to_team: int, give: Sequence[int],
            get: Sequence[int], user_drops: Sequence[int] = (), counter_to: int | None = None) -> tuple[State, Verdict]:
    """The user offers `give` for `get` to a CPU. Accepted: carried out at once. Declined: kept as an Offer
    with status "declined" (once per identical proposal per week). With `counter_to` (a pending CPU offer from
    to_team) it is a counter: that offer becomes "countered" and the new Offer carries counter_to."""
    user, give, get = rs.user_team(state), tuple(give), tuple(get)
    replay_ir.require_unblocked(state, know, user.id)
    verdict = evaluate(state, settings, know, user.id, to_team, give, get, user_drops)
    if counter_to is not None:
        state = _countered(state, to_team, counter_to)
    if verdict.accepted:
        return _apply(state, settings, know, user.id, to_team, give, get, verdict, counter_to=counter_to), verdict
    asked = counter_to is None and any(
        o.week == state.week and o.status == "declined" and (o.from_team, o.to_team) == (user.id, to_team)
        and (o.give, o.get) == (give, get) for o in state.offers)
    return (state if asked else record_offer(state, user.id, to_team, give, get, "declined", counter_to)), verdict


def offers_for_user(state: State) -> tuple[Offer, ...]:
    """CPU offers the user can still answer."""
    user = rs.user_team(state)
    return tuple(o for o in state.offers if o.to_team == user.id and o.status == "pending")


def respond(state: State, settings: Settings, know: Knowledge, offer_id: int, accept: bool,
            user_drops: Sequence[int] = ()) -> State:
    """The user answers a pending CPU offer. Accepting re-runs the CPU's test: if the offer no longer stands,
    OfferExpired (a ValueError) says why and carries the state with the offer marked "expired". A ValueError
    about the user's own drops leaves the offer open. A "countered" offer can't be answered again."""
    user = rs.user_team(state)
    offer = next((o for o in state.offers if o.id == offer_id and o.to_team == user.id), None)
    if offer is None:
        raise ValueError("There is no such offer for your team.")
    if offer.status != "pending":
        raise ValueError(f"That offer is already {offer.status}.")
    if not accept:
        return _with_status(state, offer_id, "declined")
    replay_ir.require_unblocked(state, know, user.id)              # B1: the offer stays pending
    expired = _with_status(state, offer_id, "expired")
    deal = (user.id, offer.from_team, offer.get, offer.give)        # mirrored: the user is the proposer
    try:
        if offer.week != state.week:
            raise ValueError(f"It was made in week {offer.week}.")
        _check(state, settings, know, *deal)
    except ValueError as exc:
        raise OfferExpired(f"That offer no longer stands. {exc}", expired) from exc
    verdict = evaluate(state, settings, know, *deal, user_drops)
    if not verdict.accepted:
        raise OfferExpired(f"That offer no longer stands. {' '.join(verdict.reasons)}", expired, verdict)
    return _apply(state, settings, know, *deal, verdict, offer_id)
