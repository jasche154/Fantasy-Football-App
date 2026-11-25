"""Replay League trades: the league's rules, a CPU manager's acceptance test, and the user's proposals.

`evaluate` is deterministic: a ValueError for input that cannot be a trade, else a `Verdict` whose decline
lists its reasons. Every CPU team in the deal must pass both of these, on point-in-time values only (`know`):

1. Value. `replay_trade_value.tally` on its persona's private values (less the discount on a player it is
   shopping to the user, `replay_state.markdowns`: the one place a shop changes a price) scores >= 0: what it gets (plus the
   best-player premium, a freed roster spot, half its lineup gain or its full lineup loss) against what it
   gives x a multiple x OUT_MARGIN (a 3% margin): 1.00 (normal) or 1.08 (hard) against the user, 0.95
   against another CPU; on its own offer to the user (`offered`, which `respond` passes too, so an offer
   still stands when accepted) `offer_multiple`: it pays up to its persona's top edge for what it wants.
   A player his team picked up this week counts half in every price (`replay_trade_value.fresh`).
2. Never stupid. On neutral chart values it gets back at least MARKET_GUARD of what it gives
   (`replay_trade_value.market_ok`), whatever its persona.
3. At most `replay_state.MAX_TRADES_PER_WEEK` (3) accepted trades per CPU per week; a CPU blocked by its IR (`replay_ir.blocked_reason`) declines all.

And for both teams: at most MAX_PLAYERS_PER_SIDE players a side, nobody traded in the last two weeks, a roster
within the limit (the user names who to drop; a CPU cuts its lowest healthy-chart skill players, never its
only kicker or defense, and also gets back within a position limit the deal takes it past, `replay_values.
position_cap`, IR slot included: the cut counts in its tally as one of its own), and the one position rule: every starting slot the deal leaves empty
(`replay_values.missing_slots`) has its own eligible player to fill it (`free_agents`: unrostered, not out,
and free once waivers clear). Positions need not match: a CPU sends its only tight end and picks one up
(`replay_cover.cpu_cover`, at once in the open phase, when waivers clear in the waivers phase).
Every drop goes through `replay_state.release`; a done deal or a drop expires every other pending offer naming
a player who moved (`replay_state.expire_offers`). Out players may be traded, from the active roster or an IR slot:
one leaving an IR slot frees it, and one arriving from an IR slot takes the receiver's free IR slot, else a
roster spot (`_landing`; a team blocked by a stale IR player can't trade, so every IR player dealt is eligible).

The user is always the proposer in `evaluate`: to look at a CPU's offer, mirror it (`respond` does).
"""
import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from functools import lru_cache

from services import (lineup_gain, replay_fans, replay_ir, replay_personas, replay_state as rs, replay_styles,
                      replay_trade_value as tv, replay_values as rv, trade)
from services.replay_pool import SKILL
from services.replay_state import Offer, Settings, State, Team
from services.replay_values import Knowledge

log = logging.getLogger(__name__)

TRADE_PHASES = ("waivers", "open")
MAX_PLAYERS_PER_SIDE = 6         # many-for-few deals pay a depth discount (replay_trade_value.DEPTH_SHARE)
CPU_TRADE_MULTIPLE = 0.95       # between two CPUs: each needs back at least this share of what it gives
SUGGEST_WITHIN = 1.5            # a player suggested to close a value gap is worth at most this x what is missing
VALUE_REASON = "Value is too far apart"     # every value decline starts so (a counter-offer answers only those)
DEPTH_HINT = "They'd rather have fewer, better players: offer one stronger player in place of several."
DECLINE_KINDS = ("decline_rival", "decline_room", "decline_attached", "decline_need", "decline_value")  # first wins


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
    decline: str | None = None              # why to_team said no, as a chat kind (`DECLINE_KINDS`); None when it
                                            # accepted or only a league rule or the user's own side stopped it
    decline_player: int | None = None       # the player its reply names (decline_rival / _attached / _value)
    decline_position: str | None = None     # the position it names (decline_need)


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
    mind: replay_styles.Mind | None = None  # CPU sides only


def needed_multiple(settings: Settings, other: Team) -> float:
    """The share of what it gives that a CPU needs back when dealing with `other`."""
    return rs.USER_TRADE_MULTIPLE[settings.difficulty] if other.is_user else CPU_TRADE_MULTIPLE


def offer_multiple(state: State, team: Team, counter: bool = False) -> float:
    """The multiple on a CPU's own offer to the user: it gives up to its top edge now
    (`replay_personas.edge`; `counter`: an offer answering another) more than it gets."""
    return 1.0 / ((1.0 + replay_personas.edge(state, team, counter)[1]) * tv.OUT_MARGIN)


def _multiple(state: State, settings: Settings, team: Team, other: Team, offered: bool, counter: bool = False) -> float:
    return offer_multiple(state, team, counter) if offered and other.is_user else needed_multiple(settings, other)


def fair_label(value_give: float, value_get: float) -> str:
    """trade.verdict_for on chart values for the team that sends `value_give`: "fair", "slight_win"..."""
    return trade.verdict_for(trade.percent_difference(value_give, value_get))


def _names(know: Knowledge, player_ids: Sequence[int]) -> str:
    return ", ".join(know.by_id[pid].name for pid in player_ids)


def _positions(know: Knowledge, player_ids: Sequence[int]) -> list[str]:
    return [know.by_id[pid].position for pid in player_ids]


def _chart(know: Knowledge, player_ids: Sequence[int], just_added: frozenset[int] = frozenset()) -> float:
    return sum(tv.chart(know, pid, just_added) for pid in player_ids)


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
    for owner, ids in ((sender, give), (receiver, get)):
        missing = [pid for pid in ids if pid not in rs.held(owner)]
        if missing:
            known = [know.by_id[pid].name if pid in know.by_id else f"Player {pid}" for pid in missing]
            raise ValueError(f"Not on {owner.name}'s roster: {', '.join(known)}.")
    return sender, receiver


def _landing(team: Team, other: Team, sends: tuple[int, ...], receives: tuple[int, ...]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """(active roster, IR) after the deal, before drops: a player from `other`'s IR slot takes a free IR slot."""
    roster = [pid for pid in team.roster if pid not in sends]
    ir = [pid for pid in team.ir if pid not in sends]
    for pid in receives:
        (ir if pid in other.ir and len(ir) < replay_ir.IR_SLOTS else roster).append(pid)
    return tuple(roster), tuple(ir)


def _user_drops(team: Team, settings: Settings, kept: Sequence[int], drops: tuple[int, ...]) -> tuple[int, ...]:
    """The user's own choice of who to cut, which must cover the overflow exactly."""
    over = max(0, len(kept) - settings.roster_size)
    if len(set(drops)) != len(drops) or any(pid not in team.roster or pid not in kept for pid in drops):
        raise ValueError("You can only drop players who are on your roster and not part of the trade.")
    if len(drops) != over:
        raise ValueError(f"This trade leaves you {over} over the roster limit of {settings.roster_size}: "
                         f"choose exactly {over} to drop." if over else "This trade doesn't need you to drop anyone.")
    return drops


def _over_limit(settings: Settings, know: Knowledge, roster: Sequence[int], ir: Sequence[int],
                before: Sequence[int]) -> str | None:
    """A skill position the roster and IR slot hold more of than its limit and than `before` the deal did."""
    had, held = Counter(_positions(know, before)), Counter(_positions(know, [*roster, *ir]))
    return next((pos for pos in SKILL if held[pos] > max(rv.position_cap(pos, settings), had[pos])), None)


def _cpu_drops(settings: Settings, know: Knowledge, kept: Sequence[int], ir: Sequence[int] = (),
               before: Sequence[int] = ()) -> tuple[int, ...] | None:
    """Who a CPU cuts to get back to the roster limit, and within every position limit the deal takes it past
    (`before`: what it held; Colston Loveland as a third tight end was cut the next week): its lowest
    healthy-chart skill players (at that position) that leave the lineup legal. Never a kicker or a defense.
    None when nobody can go."""
    roster, drops = list(kept), []
    while len(roster) > settings.roster_size or _over_limit(settings, know, roster, ir, before):
        crowded = _over_limit(settings, know, roster, ir, before)
        legal = rv.can_field_lineup(_positions(know, roster), settings)
        skill = sorted((know.by_id[pid] for pid in roster if know.by_id[pid].position in SKILL
                        and crowded in (None, know.by_id[pid].position)),
                       key=lambda v: (v.healthy_chart, v.healthy_value, v.ros_ppg, v.player_id))
        cut = next((v.player_id for v in skill if not legal or rv.can_field_lineup(
            _positions(know, [pid for pid in roster if pid != v.player_id]), settings)), None)
        if cut is None:
            return None
        roster.remove(cut)
        drops.append(cut)
    return tuple(drops)


def free_agents(state: State, know: Knowledge) -> list[int]:
    """Who could fill a slot a trade empties, best first: unrostered, not out, and free once waivers clear.
    In the open phase that excludes players on waivers (`state.locked`). In the waivers phase everyone
    unrostered is on waivers and the cover waits for them to clear (`replay_cover.cpu_cover`), so it is
    anyone the user hasn't claimed (a CPU's own claim may take one; the next best then covers)."""
    claimed = {claim.add for claim in state.claims if state.phase == "waivers"}
    taken = rs.rostered(state) | (claimed if state.phase == "waivers" else set(state.locked))
    pool = (v for v in know.values if v.player_id not in taken and not v.out)
    return [v.player_id for v in sorted(pool, key=lambda v: (-v.chart, -v.ros_ppg, v.player_id))]


def cover(know: Knowledge, settings: Settings, roster: Sequence[int], pool: Sequence[int],
          used: frozenset[int] = frozenset()) -> tuple[int, ...] | None:
    """One free agent from `pool` (best first) per starting slot `roster` cannot fill, none in `used`; None
    when a slot has nobody eligible. () for a legal roster."""
    picked: list[int] = []
    for slot in rv.missing_slots(_positions(know, roster), settings):
        eligible = rv.eligible(slot)
        found = next((pid for pid in pool if pid not in used and pid not in picked
                      and know.by_id[pid].position in eligible), None)
        if found is None:
            return None
        picked.append(found)
    return tuple(picked)


@lru_cache(maxsize=8)
def _seasons(know: Knowledge) -> dict[tuple[Settings, tuple[int, ...]], float]:
    """One point cache per Knowledge (immutable, hashed by identity, so entries never go stale), and only
    for the last few: each game and week has its own (the news differs by seed), and holding one keeps the
    whole Knowledge alive."""
    return {}


def _season(settings: Settings, know: Knowledge, roster: tuple[int, ...]) -> float:
    """Season-weighted points of the roster's best skill lineups. Cached: a search asks about the same
    rosters again and again."""
    cache, key = _seasons(know), (settings, roster)
    if key not in cache:
        skill = [know.by_id[pid] for pid in roster if know.by_id[pid].position in SKILL]
        cache[key] = lineup_gain.season_points(skill, rv.horizon(know, settings), rv.projector(know), know.league)
    return cache[key]


def clear_cache() -> None:
    _seasons.cache_clear()


def _side(state: State, settings: Settings, know: Knowledge, team: Team, other: Team, sends: tuple[int, ...],
          receives: tuple[int, ...], drops: tuple[int, ...], pool: Sequence[int],
          used: frozenset[int] = frozenset(), just_added: frozenset[int] = frozenset(),
          offered: bool = False, counter: bool = False,
          needs: dict[int, tuple[frozenset[str], frozenset[str]]] | None = None) -> _Side:
    after, ir = _landing(team, other, sends, receives)
    if team.is_user:
        cut = _user_drops(team, settings, after, drops)
    elif drops:
        raise ValueError("A CPU team chooses its own drops.")
    else:
        cut = _cpu_drops(settings, know, after, ir, rs.held(team))
    roster = tuple(pid for pid in after if pid not in (cut or ()))
    filled = cover(know, settings, roster, pool, used)
    gain = round(_season(settings, know, (*roster, *(filled or ()), *ir))
                 - _season(settings, know, rs.held(team)), 2)
    kept = tuple(pid for pid in receives if pid in roster or pid in ir)   # a throw-in it cuts is worth nothing
    side = _Side(team, sends, receives, cut, roster, kept, filled, gain)
    if team.is_user:
        return side
    own_cuts = tuple(pid for pid in (cut or ()) if pid not in receives)
    per_week = gain / max(1e-9, sum(rv.horizon(know, settings).weights.values()))
    mind = replay_styles.mind_for(state, settings, know, team, sends, kept, needs)
    multiple = _multiple(state, settings, team, other, offered, counter)
    counted = tv.tally(know, mind, sends, kept, own_cuts, per_week, multiple, other.is_user, team.hype,
                       rs.markdowns(state, team.id), just_added, offered and other.is_user)
    return replace(side, tally=counted, market_ok=tv.market_ok(know, sends, kept, own_cuts, just_added), mind=mind)


def _rule_reasons(state: State, know: Knowledge, sender: Team, receiver: Team, moved: tuple[int, ...]) -> list[str]:
    reasons = []
    locked = [pid for pid in moved if rs.retrade_locked(state, pid)]
    if locked:
        reasons.append(f"Traded in the last {rs.RETRADE_LOCK_WEEKS} weeks, so not yet tradable again: "
                       f"{_names(know, locked)}.")
    reasons.extend(f"{team.name} has already made {rs.MAX_TRADES_PER_WEEK} trades this week."
                   for team in (sender, receiver)
                   if not team.is_user and rs.trades_this_week(state, team.id) >= rs.MAX_TRADES_PER_WEEK)
    reasons.extend(reason for team in (sender, receiver)
                   if not team.is_user and (reason := replay_ir.blocked_reason(state, know, team.id)))
    return reasons


def _value_reason(state: State, side: _Side, other: Team, settings: Settings, know: Knowledge, offered: bool = False,
                  counter: bool = False) -> str | None:
    counted, name = side.tally, side.team.name
    if counted is not None and counted.score < 0:
        bar = _multiple(state, settings, side.team, other, offered, counter) * tv.OUT_MARGIN
        extra = f" They want {bar - 1:.0%} more than they give." if bar > 1 else ""
        if counted.lineup < 0:
            extra += f" It would weaken their starting lineup (worth {-counted.lineup:.0f} of value to them)."
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


def _rivals(know: Knowledge, side: _Side) -> list[int]:
    """Players of his rival team a fan would receive (replay_fans)."""
    return [pid for pid in side.receives if replay_fans.banned(side.team, know.by_id[pid].team)]


def _side_reasons(state: State, side: _Side, other: Team, settings: Settings, know: Knowledge, offered: bool = False,
                  counter: bool = False) -> list[str]:
    name, reasons = side.team.name, []
    hated = _rivals(know, side) if not side.team.is_user else []
    if hated:
        reasons.append(f"{name} won't take a {replay_fans.nickname(replay_fans.rival(side.team))} player: "
                       f"{_names(know, hated)}.")
    if side.drops is None:
        reasons.append(f"{name} would be over the roster or a position limit with nobody it could drop.")
    if side.cover is None:
        short = ", ".join(rv.missing_slots(_positions(know, side.roster), settings))
        reasons.append(f"It would leave {name} without a full starting lineup (short at {short}) and no free "
                       f"agent to fill it.")
    value = _value_reason(state, side, other, settings, know, offered, counter)
    return [*reasons, *([value] if value else [])]


def _decline(settings: Settings, know: Knowledge, side: _Side) -> tuple[str | None, int | None, str | None]:
    """(chat kind, player, position) for why this CPU side said no: the first of DECLINE_KINDS that applies;
    (None, None, None) when it had nothing against the deal."""
    counted, mind = side.tally, side.mind
    if counted is None or mind is None:
        return None, None, None
    hated = _rivals(know, side)
    if hated:
        return "decline_rival", hated[0], None
    if side.drops is None or side.cover is None:
        return "decline_room", None, None
    if counted.score >= 0 and side.market_ok:
        return None, None, None
    loved = [pid for pid in side.sends if replay_styles.markup(know, mind, pid) > 0]
    if loved and counted.bare_score >= 0 and side.market_ok:
        return "decline_attached", max(loved, key=lambda pid: (replay_styles.markup(know, mind, pid), -pid)), None
    if counted.lineup < 0:
        starters = {pid for ids in rv.best_lineup(know, side.team.roster, settings).values() for pid in ids}
        lost = [pid for pid in side.sends if pid in starters] or list(side.sends)
        return "decline_need", None, know.by_id[max(lost, key=lambda pid: (know.by_id[pid].chart, -pid))].position
    if side.kept and all(know.by_id[pid].position in mind.deep for pid in side.kept):
        return "decline_need", None, know.by_id[side.kept[0]].position
    return "decline_value", max(side.sends, key=lambda pid: (know.by_id[pid].chart, -pid)), None


def _points_text(points: float) -> str:
    whole = max(1, round(points))
    return f"Add about {whole} point{'' if whole == 1 else 's'} of value to make this fair."


def _suggestion(state: State, settings: Settings, know: Knowledge, mine: _Side, theirs: _Side,
                just_added: frozenset[int] = frozenset()) -> str | None:
    """How the user could fix a value decline by to_team: add the one of his spare skill players worth
    closest to what is missing (at most SUGGEST_WITHIN x it) whose counted worth to them (after the depth
    discount and any player of theirs he makes them cut) covers it, else that many points."""
    counted = theirs.tally
    if counted is None or not mine.team.is_user:
        return None
    def worth_of(pid: int) -> float:
        return tv.chart(know, pid, just_added)

    sent_charts = [worth_of(pid) for pid in theirs.sends]
    got = tv.depth_counted([worth_of(pid) for pid in theirs.kept], sent_charts)
    missing = max(-counted.score, tv.MARKET_GUARD * sum(sent_charts) - got)
    if missing <= 0:
        return None
    if got < _chart(know, theirs.kept, just_added):             # the depth discount bit: more pieces count little, ask for better ones
        return DEPTH_HINT
    if len(mine.sends) >= MAX_PLAYERS_PER_SIDE:
        return _points_text(missing)
    def worth(receives: tuple[int, ...]) -> float:  # what they count receiving these for, net of cuts, spots freed
        after, ir = _landing(theirs.team, mine.team, theirs.sends, receives)      # as _side lands the deal
        drops = _cpu_drops(settings, know, after, ir, rs.held(theirs.team)) or ()
        kept = [worth_of(pid) for pid in receives if pid not in drops]
        return (tv.depth_counted(kept, sent_charts) + tv.SPOT_VALUE * max(0, len(theirs.sends) - len(kept))
                - _chart(know, [pid for pid in drops if pid not in receives], just_added))

    now = worth(theirs.receives)
    spare = [know.by_id[pid] for pid in mine.team.roster if pid not in mine.sends and pid not in (mine.drops or ())
             and not rs.retrade_locked(state, pid)]
    enough = [v for v in spare if v.position in SKILL and not v.out
              and missing <= worth_of(v.player_id) <= SUGGEST_WITHIN * missing
              and worth((*theirs.receives, v.player_id)) - now >= missing]
    if enough:
        closest = min(enough, key=lambda v: (worth_of(v.player_id) - missing, v.player_id))
        return f"Add {closest.name} to make this fair."
    return _points_text(missing)


def evaluate(state: State, settings: Settings, know: Knowledge, from_team: int, to_team: int,
             give: Sequence[int], get: Sequence[int], proposer_drops: Sequence[int] = (),
             offered: bool = False, counter: bool = False) -> Verdict:
    """Would this deal go through? from_team sends `give` and receives `get`; to_team is a CPU.
    `offered`: the deal is to_team's own offer to the user (mirrored), judged at `offer_multiple`; `counter`:
    that offer answers another (a haggler's counter gives his normal edge).

    ValueError for input that cannot be a trade (wrong roster, too many players, past the deadline, the
    user's drops not covering the overflow). Otherwise a Verdict, the same one every time it is asked."""
    give, get = tuple(give), tuple(get)
    sender, receiver = _check(state, settings, know, from_team, to_team, give, get)
    pool, just_added = free_agents(state, know), tv.fresh(know, rs.pickups(state))
    needs = replay_styles.position_needs(state, settings, know)
    mine = _side(state, settings, know, sender, receiver, give, get, tuple(proposer_drops), pool,
                 just_added=just_added, offered=offered, counter=counter, needs=needs)
    theirs = _side(state, settings, know, receiver, sender, get, give, (), pool, frozenset(mine.cover or ()),
                   just_added, offered, counter, needs)
    reasons = [*_rule_reasons(state, know, sender, receiver, (*give, *get)),
               *_side_reasons(state, theirs, sender, settings, know, offered, counter),
               *_side_reasons(state, mine, receiver, settings, know)]
    decline = _decline(settings, know, theirs) if reasons else (None, None, None)
    bonus = tv.premium(know, "balanced", give, mine.kept, just_added)
    value_give = round(_chart(know, give, just_added) + max(0.0, -bonus), 2)
    value_get = round(_chart(know, mine.kept, just_added) - _chart(know, mine.drops or (), just_added)
                      + max(0.0, bonus), 2)
    scores = [side.tally.score for side in (mine, theirs) if side.tally is not None]
    return Verdict(
        accepted=not reasons, reasons=tuple(reasons), value_give=value_give, value_get=value_get,
        percent=trade.percent_difference(value_give, value_get), their_gain=theirs.gain, your_gain=mine.gain,
        their_drops=theirs.drops or (), fair_label=fair_label(value_give, value_get),
        your_drops=mine.drops or (),
        give_values=tuple((pid, tv.chart(know, pid, just_added)) for pid in give),
        get_values=tuple((pid, tv.chart(know, pid, just_added)) for pid in get),
        suggestion=_suggestion(state, settings, know, mine, theirs, just_added), score=min(scores, default=0.0),
        decline=decline[0], decline_player=decline[1], decline_position=decline[2])


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
    """An accepted deal carried out: rosters (and IR slots) swapped, re-trade lock set, Offer "accepted", every
    other pending offer naming a player who moved "expired", event, forced drops."""
    sender, receiver = rs.team(state, from_team), rs.team(state, to_team)
    for team, other, sends, receives in ((sender, receiver, give, get), (receiver, sender, get, give)):
        roster, ir = _landing(team, other, sends, receives)
        state = rs.with_team(state, replace(team, roster=roster, ir=ir))
    state = replace(state, traded={**state.traded, **dict.fromkeys((*give, *get), state.week)})
    state = (record_offer(state, from_team, to_team, give, get, "accepted", counter_to) if offer_id is None
             else _with_status(state, offer_id, "accepted"))
    state = rs.expire_offers(state, (*give, *get))
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
    verdict = evaluate(state, settings, know, *deal, user_drops, offered=True, counter=offer.counter_to is not None)
    if not verdict.accepted:
        raise OfferExpired(f"That offer no longer stands. {' '.join(verdict.reasons)}", expired, verdict)
    return _apply(state, settings, know, *deal, verdict, offer_id)
