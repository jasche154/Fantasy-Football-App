"""Replay League trades: the league's rules, a CPU manager's acceptance test, and the user's proposals.

`evaluate` is deterministic (no dice), so asking twice never changes the answer. It first rejects input that
cannot be a trade at all (ValueError with a sentence for the user), then returns a `Verdict`; a decline lists
its reasons. Every CPU team in the deal must pass all of these, on point-in-time values only (`know`):

1. Value. The package it receives is worth at least the package it gives x a multiple: 1.00 (normal) or 1.08
   (hard) against the user, 0.95 against another CPU. A package is `replay_values.market_value` per player,
   best first, weighted by `trade.DEPTH_WEIGHTS`. Kickers and defenses count for a roster spot only, and a
   CPU won't send its kicker or defense for a weaker one. A player it receives and cuts at once counts for
   nothing.
2. Lineup. Its rest-of-season lineup points (`lineup_gain`, skill positions, after any forced drop) rise by at
   least MIN_TRADE_GAIN. This is what stops a team with six bench receivers taking a seventh.
3. One accepted trade per CPU per week.
4. Depth. It never ends up with more players at a position it is already full at (`replay_values.crowded`:
   2 QB, 6 RB, 7 WR, 2 TE, 1 K, 1 DST, unless nobody it has there plays this week).

And for both teams: at most MAX_PLAYERS_PER_SIDE players a side, nobody traded in the last two weeks, a full
legal starting lineup at all six positions afterwards, a roster within the limit (the user names who to drop;
a CPU cuts its lowest healthy-value skill players, never its only kicker or defense), and, in every deal (CPU-CPU
too), nobody who is out right now. Every drop goes through `replay_state.release`, so he is on waivers.
IR players can't be traded; they still count for a CPU's lineup gain and its depth at a position.

The user is always the proposer in `evaluate`: to look at a CPU's offer, mirror it (`respond` does).
"""
import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from functools import lru_cache

from services import lineup_gain, replay_ir, replay_state as rs, replay_values as rv, trade
from services.replay_pool import SKILL
from services.replay_state import Offer, Settings, State, Team
from services.replay_values import Knowledge

log = logging.getLogger(__name__)

TRADE_PHASES = ("waivers", "open")
MAX_PLAYERS_PER_SIDE = 3
CPU_TRADE_MULTIPLE = 0.95       # between two CPUs: each needs back at least this share of what it gives
MIN_TRADE_GAIN = 0.5            # season-weighted lineup points a CPU must gain
SUGGEST_WITHIN = 1.5            # a player suggested to close a value gap is worth at most this x what is missing


@dataclass(frozen=True)
class Verdict:
    """The answer to "from_team sends `give` and receives `get`". "Your" is from_team, "their" is to_team."""
    accepted: bool
    reasons: tuple[str, ...]                # why not, as plain sentences; empty when accepted
    value_give: float                       # package value from_team sends
    value_get: float                        # package value from_team receives
    percent: float                          # (get - give) / the larger: above 0, from_team gets more
    their_gain: float                       # season-weighted lineup points, after drops
    your_gain: float
    their_drops: tuple[int, ...]            # who to_team (a CPU) would cut to stay within the roster limit
    fair_label: str                         # trade.verdict_for, for from_team: "fair", "slight_win", "big_loss"...
    your_drops: tuple[int, ...] = ()
    give_values: tuple[tuple[int, float], ...] = ()     # (player_id, market_value), in the order given
    get_values: tuple[tuple[int, float], ...] = ()
    suggestion: str | None = None           # a hint that would fix a value decline


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
    sent: float
    received: float
    gain: float


def package_value(know: Knowledge, player_ids: Sequence[int]) -> float:
    """What these players count for together: market values, best first, weighted by trade.DEPTH_WEIGHTS."""
    return trade.adjusted_total([rv.market_value(know, pid) for pid in player_ids])


def needed_multiple(settings: Settings, other: Team) -> float:
    """The share of what it gives that a CPU needs back when dealing with `other`."""
    return rs.USER_TRADE_MULTIPLE[settings.difficulty] if other.is_user else CPU_TRADE_MULTIPLE


def fair_label(know: Knowledge, value_give: float, value_get: float) -> str:
    """trade.verdict_for for the team that sends `value_give`: "fair", "slight_win", "big_loss"..."""
    percent = trade.percent_difference(value_give, value_get)
    return trade.verdict_for(percent, value_get - value_give, know.weeks_remaining)


def _names(know: Knowledge, player_ids: Sequence[int]) -> str:
    return ", ".join(know.by_id[pid].name for pid in player_ids)


def _positions(know: Knowledge, player_ids: Sequence[int]) -> list[str]:
    return [know.by_id[pid].position for pid in player_ids]


def _check(state: State, settings: Settings, know: Knowledge, from_team: int, to_team: int,
           give: tuple[int, ...], get: tuple[int, ...]) -> tuple[Team, Team]:
    """(sender, receiver), or ValueError when this cannot be a trade at all."""
    if state.phase not in TRADE_PHASES:
        raise ValueError("Trades are open from the end of the draft to the end of the season.")
    if state.week > settings.trade_deadline_week:
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
    """Who a CPU cuts to get back to the roster limit: its lowest healthy-value skill players that leave the
    lineup legal. Never a kicker or a defense. None when nobody can go."""
    roster, drops = list(kept), []
    while len(roster) > settings.roster_size:
        legal = rv.can_field_lineup(_positions(know, roster), settings)
        skill = sorted((know.by_id[pid] for pid in roster if know.by_id[pid].position in SKILL),
                       key=lambda v: (v.healthy_value, v.ros_ppg, v.player_id))
        cut = next((v.player_id for v in skill if not legal or rv.can_field_lineup(
            _positions(know, [pid for pid in roster if pid != v.player_id]), settings)), None)
        if cut is None:
            return None
        roster.remove(cut)
        drops.append(cut)
    return tuple(drops)


@lru_cache(maxsize=1024)
def _season(settings: Settings, know: Knowledge, roster: tuple[int, ...]) -> float:
    """Season-weighted points of the roster's best skill lineups. Cached: a search asks about the same
    rosters again and again (a Knowledge is immutable and hashed by identity, so entries never go stale)."""
    skill = [know.by_id[pid] for pid in roster if know.by_id[pid].position in SKILL]
    return lineup_gain.season_points(skill, rv.horizon(know, settings), rv.projector(know), know.league)


def clear_cache() -> None:
    _season.cache_clear()


def _side(settings: Settings, know: Knowledge, team: Team, sends: tuple[int, ...], receives: tuple[int, ...],
          drops: tuple[int, ...]) -> _Side:
    kept = (*(pid for pid in team.roster if pid not in sends), *receives)
    if team.is_user:
        cut = _user_drops(team, settings, kept, drops)
    elif drops:
        raise ValueError("A CPU team chooses its own drops.")
    else:
        cut = _cpu_drops(settings, know, kept)
    roster = tuple(pid for pid in kept if pid not in (cut or ()))
    gain = _season(settings, know, (*roster, *team.ir)) - _season(settings, know, rs.held(team))
    kept_in = [pid for pid in receives if pid not in (cut or ())]      # a throw-in it cuts at once is worth nothing
    return _Side(team, sends, receives, cut, roster, package_value(know, sends), package_value(know, kept_in),
                 round(gain, 2))


def _rule_reasons(state: State, know: Knowledge, sender: Team, receiver: Team, moved: tuple[int, ...]) -> list[str]:
    reasons = []
    locked = [pid for pid in moved if rs.retrade_locked(state, pid)]
    if locked:
        reasons.append(f"Traded in the last {rs.RETRADE_LOCK_WEEKS} weeks, so not yet tradable again: "
                       f"{_names(know, locked)}.")
    out = [pid for pid in moved if know.by_id[pid].out]
    if out:
        reasons.append(f"They won't deal for or away a player who is out right now: {_names(know, out)}.")
    reasons.extend(f"{team.name} has already made a trade this week." for team in (sender, receiver)
                   if not team.is_user and rs.has_traded_this_week(state, team.id))
    return reasons


def _short_at(settings: Settings, know: Knowledge, roster: Sequence[int]) -> str:
    have = Counter(_positions(know, roster))
    short = [slot for slot, wanted in settings.starters.items() if slot != "FLEX" and have[slot] < wanted]
    return ", ".join(short) or "FLEX"


def _gain_reason(side: _Side, settings: Settings, know: Knowledge) -> str:
    """Why the deal does too little for this CPU's lineup, naming a position it gets but is already deep at."""
    name = side.team.name
    if side.gain < 0:
        return f"It would make {name}'s lineup worse."
    have, flex = Counter(_positions(know, rs.held(side.team))), settings.starters.get("FLEX", 0)
    room = {position: settings.starters.get(position, 0) + (flex if position in rv.FLEX_POSITIONS else 0)
            for position in SKILL}
    deep = sorted(position for position in set(_positions(know, side.receives))
                  if position in SKILL and have[position] > room[position])
    hint = f": they are already deep at {' and '.join(deep)}" if deep else ""
    return f"It doesn't improve {name}'s lineup{hint}."


def _side_reasons(side: _Side, other: Team, settings: Settings, know: Knowledge) -> list[str]:
    name, reasons = side.team.name, []
    if side.drops is None:
        reasons.append(f"{name} would be over the roster limit with nobody it could drop.")
    if not rv.can_field_lineup(_positions(know, side.roster), settings):
        reasons.append(f"It would leave {name} without a full starting lineup "
                       f"(short at {_short_at(settings, know, side.roster)}).")
    if side.team.is_user:
        return reasons                       # the user needs no value or lineup test: their choice
    ir = side.team.ir                       # positional limits count the IR player: he is coming back
    had, have = Counter(_positions(know, rs.held(side.team))), Counter(_positions(know, (*side.roster, *ir)))
    kept = [know.by_id[pid] for pid in (*side.roster, *ir) if pid not in side.receives]
    new = [know.by_id[pid] for pid in side.receives if pid in side.roster]
    reasons.extend(f"It would leave {name} with {have[position]} at {position}, more than they carry."
                   for position in sorted({value.position for value in new})
                   if have[position] > had[position] and rv.crowded(kept, new, position, settings))
    for position, unit in (("K", "kicker"), ("DST", "defense")):        # worth a roster spot, but not a downgrade
        best = [max((know.by_id[pid].ros_ppg for pid in roster if know.by_id[pid].position == position), default=0.0)
                for roster in (side.team.roster, side.roster)]
        if position in _positions(know, side.sends) and best[1] < best[0]:
            reasons.append(f"{name} won't swap its {unit} for a weaker one.")
    multiple = needed_multiple(settings, other)
    if side.received < side.sent * multiple:
        extra = f" They want {multiple - 1:.0%} more than they give." if multiple > 1 else ""
        cut = [pid for pid in side.receives if pid in (side.drops or ())]
        if cut:
            extra += f" They would drop {_names(know, cut)} to make room, so that counts for nothing."
        reasons.append(f"Value is too far apart: {name} would give {side.sent:.1f} points of value and get "
                       f"{side.received:.1f} back.{extra}")
    if side.gain < MIN_TRADE_GAIN:
        reasons.append(_gain_reason(side, settings, know))
    return reasons


def _points_text(points: float) -> str:
    whole = max(1, round(points))
    return f"Add about {whole} point{'' if whole == 1 else 's'} of value to make this fair."


def _suggestion(state: State, settings: Settings, know: Knowledge, mine: _Side, theirs: _Side) -> str | None:
    """How the proposer could fix a value decline by to_team: add the one of his skill players worth closest
    to what one more player must be worth to close the gap, else that many points of value. A small gap
    never asks for a star: he is worth at most SUGGEST_WITHIN times what is missing, or a roster spot."""
    needed = theirs.sent * needed_multiple(settings, mine.team)
    if theirs.received >= needed:
        return None
    if len(mine.sends) >= MAX_PLAYERS_PER_SIDE:              # nobody can be added: the package must get better
        return _points_text(needed - theirs.received)
    sending = [rv.market_value(know, pid) for pid in mine.sends if pid not in (theirs.drops or ())]
    low, missing = 0.0, needed                               # as the best player, `needed` alone closes it
    for _ in range(trade.EVEN_IT_STEPS):                     # the package discount makes it more than the gap
        middle = (low + missing) / 2
        low, missing = (low, middle) if trade.adjusted_total([*sending, middle]) >= needed else (middle, missing)
    spare = [know.by_id[pid] for pid in mine.team.roster if pid not in mine.sends
             and not rs.retrade_locked(state, pid)]
    most = max(SUGGEST_WITHIN * missing, trade.FAIR_POINTS_PER_WEEK * know.weeks_remaining)
    enough = [value for value in spare if value.position in SKILL and not value.out
              and value.market_value <= most and trade.adjusted_total([*sending, value.market_value]) >= needed]
    if enough:
        closest = min(enough, key=lambda v: (abs(v.market_value - missing), v.player_id))
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
    mine = _side(settings, know, sender, give, get, tuple(proposer_drops))
    theirs = _side(settings, know, receiver, get, give, ())
    reasons = [*_rule_reasons(state, know, sender, receiver, (*give, *get)),
               *_side_reasons(theirs, sender, settings, know), *_side_reasons(mine, receiver, settings, know)]
    return Verdict(
        accepted=not reasons, reasons=tuple(reasons), value_give=mine.sent, value_get=mine.received,
        percent=trade.percent_difference(mine.sent, mine.received), their_gain=theirs.gain, your_gain=mine.gain,
        their_drops=theirs.drops or (), fair_label=fair_label(know, mine.sent, mine.received),
        your_drops=mine.drops or (),
        give_values=tuple((pid, rv.market_value(know, pid)) for pid in give),
        get_values=tuple((pid, rv.market_value(know, pid)) for pid in get),
        suggestion=_suggestion(state, settings, know, mine, theirs))


def record_offer(state: State, from_team: int, to_team: int, give: Sequence[int], get: Sequence[int],
                 status: str) -> State:
    """The state with a new Offer for this week (`give` is what from_team sends)."""
    offer = Offer(id=state.next_id, week=state.week, from_team=from_team, to_team=to_team, give=tuple(give),
                  get=tuple(get), status=status)
    return replace(state, offers=(*state.offers, offer), next_id=state.next_id + 1)


def _with_status(state: State, offer_id: int, status: str) -> State:
    return replace(state, offers=tuple(replace(o, status=status) if o.id == offer_id else o for o in state.offers))


def _apply(state: State, settings: Settings, know: Knowledge, from_team: int, to_team: int, give: tuple[int, ...],
           get: tuple[int, ...], verdict: Verdict, offer_id: int | None = None) -> State:
    """An accepted deal carried out: rosters swapped, re-trade lock set, Offer "accepted", event, forced drops."""
    sender, receiver = rs.team(state, from_team), rs.team(state, to_team)
    state = rs.with_team(state, replace(sender, roster=(*(p for p in sender.roster if p not in give), *get)))
    state = rs.with_team(state, replace(receiver, roster=(*(p for p in receiver.roster if p not in get), *give)))
    state = replace(state, traded={**state.traded, **dict.fromkeys((*give, *get), state.week)})
    state = (record_offer(state, from_team, to_team, give, get, "accepted") if offer_id is None
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


def propose(state: State, settings: Settings, know: Knowledge, to_team: int, give: Sequence[int],
            get: Sequence[int], user_drops: Sequence[int] = ()) -> tuple[State, Verdict]:
    """The user offers `give` for `get` to a CPU. Accepted: carried out at once. Declined: kept as an Offer
    with status "declined" (once per identical proposal per week)."""
    user, give, get = rs.user_team(state), tuple(give), tuple(get)
    replay_ir.require_unblocked(state, know, user.id)
    verdict = evaluate(state, settings, know, user.id, to_team, give, get, user_drops)
    if verdict.accepted:
        return _apply(state, settings, know, user.id, to_team, give, get, verdict), verdict
    asked = any(o.week == state.week and o.status == "declined" and (o.from_team, o.to_team) == (user.id, to_team)
                and (o.give, o.get) == (give, get) for o in state.offers)
    return (state if asked else record_offer(state, user.id, to_team, give, get, "declined")), verdict


def offers_for_user(state: State) -> tuple[Offer, ...]:
    """CPU offers the user can still answer."""
    user = rs.user_team(state)
    return tuple(o for o in state.offers if o.to_team == user.id and o.status == "pending")


def respond(state: State, settings: Settings, know: Knowledge, offer_id: int, accept: bool,
            user_drops: Sequence[int] = ()) -> State:
    """The user answers a pending CPU offer. Accepting re-runs the CPU's test: if the offer no longer stands,
    OfferExpired (a ValueError) says why and carries the state with the offer marked "expired". A ValueError
    about the user's own drops leaves the offer open."""
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
