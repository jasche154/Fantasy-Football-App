"""Replay League trades: why a deal is declined, as sentences (`rule_reasons`, `side_reasons`) and as the one reason a
CPU gives in its reply (`decline`). Re-exported by replay_trades, which builds each team's `Side`.

`rule_decline` comes first: a league rule that blocks the deal whatever anyone thinks of it (a player traded in the
last 2 weeks, the CPU's weekly trade cap, its IR to sort out, a hole the deal leaves in the user's own lineup), so
the reply never blames a persona for a rule. Then `decline` names the first of DECLINE_KINDS that is true, testing
one thing at a time: a fan's rival player in the deal; no roster or lineup room; else, only when its value test
failed, whether taking one term away would have flipped it: its "high on" mark-ups (decline_attached), its lineup
loss (decline_need, the position of the best starter it sends; also when that loss is bigger than the value gap
left without it, `lineup_led`), or, when it would only get positions where it is deep, counting what it gets as
filling a need (decline_need, that position); else decline_close when it needed at most CLOSE_SHARE more than it
was offered, decline_value (a lowball) when further apart, naming the best player it was asked for. Pure.
"""
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from services import replay_fans, replay_ir, replay_state as rs, replay_styles, replay_trade_value as tv
from services import replay_values as rv
from services.replay_state import Settings, State, Team
from services.replay_trade_rosters import positions
from services.replay_values import Knowledge

VALUE_REASON = "Value is too far apart"     # every value decline starts so (a counter-offer answers only those)
RULE_KINDS = ("decline_rule_traded", "decline_rule_cap", "decline_rule_ir", "decline_rule_lineup")
DECLINE_KINDS = (*RULE_KINDS, "decline_rival", "decline_room", "decline_attached", "decline_need", "decline_close",
                 "decline_value")                     # first wins
CLOSE_SHARE = 0.15          # a value decline this close (missing at most 15% of what he was offered) says so
Decline = tuple[str | None, int | None, str | None]       # (chat kind, player it names, position it names)


@dataclass(frozen=True)
class Side:
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
    rescore: Callable[..., tv.Tally] | None = None      # its tally again with some inputs changed (CPU sides only)


def names(know: Knowledge, player_ids: Sequence[int]) -> str:
    return ", ".join(know.by_id[pid].name for pid in player_ids)


def rule_reasons(state: State, know: Knowledge, sender: Team, receiver: Team, moved: tuple[int, ...]) -> list[str]:
    reasons = []
    locked = [pid for pid in moved if rs.retrade_locked(state, pid)]
    if locked:
        reasons.append(f"Traded in the last {rs.RETRADE_LOCK_WEEKS} weeks, so not yet tradable again: "
                       f"{names(know, locked)}.")
    reasons.extend(f"{team.name} has already made {rs.MAX_TRADES_PER_WEEK} trades this week."
                   for team in (sender, receiver)
                   if not team.is_user and rs.trades_this_week(state, team.id) >= rs.MAX_TRADES_PER_WEEK)
    reasons.extend(reason for team in (sender, receiver)
                   if not team.is_user and (reason := replay_ir.blocked_reason(state, know, team.id)))
    return reasons


def lineup_led(side: Side) -> bool:
    """Did losing lineup value sink the deal? Without that loss it passes, or the loss is bigger than the gap left."""
    counted = side.tally
    if counted is None or counted.lineup >= 0 or side.rescore is None or not side.market_ok:
        return False
    rest = side.rescore(gain_per_week=0.0).score
    return rest >= 0 or -counted.lineup > -rest


def _value_reason(side: Side, other: Team, know: Knowledge, bar: float) -> str | None:
    """`bar`: the share of what it gives it needs back (its multiple x OUT_MARGIN). Leads with the lineup loss when
    that is what sank it (`lineup_led`)."""
    counted, name = side.tally, side.team.name
    if counted is not None and counted.score < 0:
        weakens = f"It would weaken their starting lineup (worth {-counted.lineup:.0f} of value to them)."
        led = lineup_led(side)
        extra = f" They want {bar - 1:.0%} more than they give." if bar > 1 else ""
        if counted.lineup < 0 and not led:
            extra += f" {weakens}"
        cut = [pid for pid in side.receives if pid not in side.kept]
        if cut:
            extra += f" They would drop {names(know, cut)} to make room, so that counts for nothing."
        held = [pid for pid in side.sends if other.is_user and know.by_id[pid].out]
        if held:
            extra += (f" They won't let {names(know, held)} go for less than {tv.OUT_SELL_SHARE:.0%} of "
                      f"healthy value while out.")
        values = (f"{name} would give {counted.sent + max(0.0, -counted.premium):.0f} of value and "
                  f"get {counted.received + max(0.0, counted.premium):.0f} back.")
        return f"{VALUE_REASON}: {weakens} {values}{extra}" if led else f"{VALUE_REASON}: {values}{extra}"
    if not side.market_ok:
        return f"{VALUE_REASON}: {name} won't take back less than {tv.MARKET_GUARD:.0%} of what it gives."
    return None


def rivals(know: Knowledge, side: Side) -> list[int]:
    """Players of his rival team a fan would receive (replay_fans)."""
    return [pid for pid in side.receives if replay_fans.banned(side.team, know.by_id[pid].team)]


def side_reasons(side: Side, other: Team, settings: Settings, know: Knowledge, bar: float) -> list[str]:
    name, reasons = side.team.name, []
    hated = rivals(know, side) if not side.team.is_user else []
    if hated:
        reasons.append(f"{name} won't take a {replay_fans.nickname(replay_fans.rival(side.team))} player: "
                       f"{names(know, hated)}.")
    if side.drops is None:
        reasons.append(f"{name} would be over the roster or a position limit with nobody it could drop.")
    if side.cover is None:
        short = ", ".join(rv.missing_slots(positions(know, side.roster), settings))
        reasons.append(f"It would leave {name} without a full starting lineup (short at {short}) and no free "
                       f"agent to fill it.")
    value = _value_reason(side, other, know, bar)
    return [*reasons, *([value] if value else [])]


def best_of(know: Knowledge, player_ids: Sequence[int]) -> int:
    return max(player_ids, key=lambda pid: (know.by_id[pid].chart, -pid))


def rule_decline(state: State, settings: Settings, know: Knowledge, receiver: Team, moved: tuple[int, ...],
                 mine: Side) -> Decline | None:
    """The league rule that blocks the deal, as (kind, player, position), else None (`mine`: the proposer's side)."""
    locked = [pid for pid in moved if rs.retrade_locked(state, pid)]
    if locked:
        return "decline_rule_traded", locked[0], None
    if not receiver.is_user and rs.trades_this_week(state, receiver.id) >= rs.MAX_TRADES_PER_WEEK:
        return "decline_rule_cap", None, None
    if not receiver.is_user and replay_ir.blocked_reason(state, know, receiver.id):
        return "decline_rule_ir", None, None
    if mine.cover is None:
        return "decline_rule_lineup", None, rv.missing_slots(positions(know, mine.roster), settings)[0]
    return None


def decline(settings: Settings, know: Knowledge, side: Side) -> Decline:
    """Why this CPU side said no, the first of DECLINE_KINDS that is true; (None, None, None) when it had nothing
    against the deal (only a league rule or the other side stopped it)."""
    counted, mind = side.tally, side.mind
    if counted is None or mind is None or side.rescore is None:
        return None, None, None
    hated = rivals(know, side)
    if hated:
        return "decline_rival", hated[0], None
    if side.drops is None or side.cover is None:
        return "decline_room", None, None
    if counted.score >= 0 and side.market_ok:
        return None, None, None

    def takes(found: tv.Tally) -> bool:
        return side.market_ok and found.score >= 0

    loved = [pid for pid in side.sends if replay_styles.markup(know, mind, pid) > 0]
    if loved and takes(side.rescore(attach=False)):
        return "decline_attached", max(loved, key=lambda pid: (replay_styles.markup(know, mind, pid), -pid)), None
    if lineup_led(side):
        starters = rv.starters(know, side.team.roster, settings)
        return "decline_need", None, know.by_id[best_of(know, [p for p in side.sends if p in starters] or side.sends)].position
    if side.kept and all(know.by_id[pid].position in mind.deep for pid in side.kept):
        wanted = {know.by_id[pid].position for pid in side.kept}
        filled = replace(mind, needs=mind.needs | wanted, deep=mind.deep - wanted, filling=frozenset(side.kept))
        if takes(side.rescore(persona=filled)):
            return "decline_need", None, know.by_id[side.kept[0]].position
    offered = counted.received + max(0.0, counted.premium)
    close = side.market_ok and -counted.score <= CLOSE_SHARE * max(offered, 1.0)
    return ("decline_close" if close else "decline_value"), best_of(know, side.sends), None
