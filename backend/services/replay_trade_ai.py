"""Replay League: the trades CPU managers make on their own, once a week.

`cpu_trades` makes up to CPU_ATTEMPTS attempts at a CPU-CPU deal (the first always, each later one with
probability EXTRA_ATTEMPT_CHANCE) and then, with probability USER_OFFER_CHANCE, one CPU sends the user an
offer. An attempt asks up to MAX_PAIRS seeded pairs of CPUs that have not traded this week and makes the
first pair's deal that it finds. Each pair is searched within fixed bounds:

1. Each side's TOP_SKILL most valuable skill players that may be traded (out players too, from the active
   roster); packages of one or two of them.
2. Keep the deals whose package values are fair for both sides (cheap: no lineup maths; a CPU counts an out
   player it receives at replay_trades.OUT_RISK_DISCOUNT).
3. Run the full `replay_trades.evaluate` on the PAIR_SCORED (MAX_SCORED for the user) with the smallest
   value gap.
4. Drop the ones worth less than MIN_CPU_GAIN to a CPU's lineup (a CPU accepts a deal for MIN_TRADE_GAIN, but
   it does not go looking for one that small) and the ones that would leave a CPU a starting slot nobody
   plays in this week, then take the one that gives the larger gain to whichever lineup it helps less.

An offer to the user uses the same search with the user as one side: the CPU must pass its own acceptance
test, the deal must be value-fair for the user (`fair_for_user`) and need no drop from the user. It is stored as a pending Offer; nothing changes hands until `replay_trades.respond`.
"""
from collections.abc import Sequence
from itertools import combinations

import numpy as np

from services import replay_state as rs, replay_trades as rt
from services.replay_pool import SKILL
from services.replay_state import Settings, State, Team
from services.replay_values import Knowledge, ReplayValue

CPU_ATTEMPTS = 2
EXTRA_ATTEMPT_CHANCE = 0.5      # each attempt after the first
MAX_PAIRS = 3                   # pairs of CPUs one attempt asks before it gives up. One pair has a deal about
                                # one time in seven: asking 1 pair, real seasons had 0-8 trades (mean 2.4) and
                                # one in eight had none; asking 3 they have 1-8 (mean 4.1) and none had none
USER_OFFER_CHANCE = 0.35        # a week
TOP_SKILL = 8                   # players per team the search may move
PACKAGE_SIZES = (1, 2)          # 1-for-1, 2-for-1, 1-for-2 and 2-for-2
MAX_SCORED = 12                 # value-fair deals that get the full lineup evaluation for an offer to the user
PAIR_SCORED = 8                 # and for each pair of CPUs (MAX_PAIRS of them cost what two pairs at 12 would)
MIN_CPU_GAIN = 2.0              # season-weighted lineup points a CPU must gain to go looking for a deal itself
                                # (tuned on whole simulated games, about 3 trades a season; at 5.0 it was 1.5
                                # and one season in five had none)

Deal = tuple[tuple[int, ...], tuple[int, ...]]      # (what the first team sends, what it receives)
Package = tuple[float, tuple[int, ...], float, float]   # (as sent, ids, as a CPU receives it, as a CPU sends
                                                        # it to the user)


def _tradable(state: State, know: Knowledge, team: Team) -> list[ReplayValue]:
    """The team's most valuable skill players that may move: not re-trade locked (out players may: an IR slot
    is not on the roster)."""
    values = [value for pid in team.roster if (value := know.by_id[pid]).position in SKILL
              and not rs.retrade_locked(state, pid)]
    return sorted(values, key=lambda v: (-v.market_value, v.player_id))[:TOP_SKILL]


def _packages(know: Knowledge, values: Sequence[ReplayValue]) -> list[Package]:
    """(value as sent, ids, value as a CPU counts it coming in, value as a CPU counts it going to the user)."""
    groups = (tuple(v.player_id for v in combo) for size in PACKAGE_SIZES for combo in combinations(values, size))
    return [(rt.package_value(know, ids), ids, rt.package_value(know, ids, True),
             rt.package_value(know, ids, cpu_sends=True)) for ids in groups]


def fair_for_user(settings: Settings, know: Knowledge, value_give: float, value_get: float) -> bool:
    """Is a CPU's offer fair for the user, who sends `value_give`? Yes when trade.verdict_for calls it fair
    or better once the premium a CPU asks of the user (none on normal, 8% on hard) is set aside."""
    asked = rs.USER_TRADE_MULTIPLE[settings.difficulty]
    return not rt.fair_label(know, value_give, value_get * asked).endswith("loss")


def _fair_to_sender(settings: Settings, know: Knowledge, sender: Team, receiver: Team, give: Package,
                    get: Package) -> bool:
    """A CPU sender needs its multiple back. The user needs a fair deal that needs no drop."""
    if not sender.is_user:
        return get[2] >= give[0] * rt.needed_multiple(settings, receiver)
    fits = len(sender.roster) - len(give[1]) + len(get[1]) <= settings.roster_size
    return fits and fair_for_user(settings, know, give[0], get[0])


def _fair_deals(state: State, settings: Settings, know: Knowledge, sender: Team, receiver: Team) -> list[Deal]:
    """Value-fair deals between the two, smallest value gap first. `receiver` is a CPU."""
    mine = _packages(know, _tradable(state, know, sender))
    theirs = _packages(know, _tradable(state, know, receiver))
    wanted, sent = rt.needed_multiple(settings, sender), 3 if sender.is_user else 0     # its count of what it sends
    fair = [(abs(give[0] - get[0]), len(give[1]) + len(get[1]), give[1], get[1])
            for give in mine for get in theirs
            if give[2] >= get[sent] * wanted and _fair_to_sender(settings, know, sender, receiver, give, get)]
    return [(give, get) for _, _, give, get in sorted(fair)]


def _best_deal(state: State, settings: Settings, know: Knowledge, sender: Team, receiver: Team,
               scored: int = MAX_SCORED) -> Deal | None:
    """The accepted deal that does most for the lineup it helps less (ties: the smaller value gap), among
    the `scored` value-fair deals with the smallest gap."""
    found: list[tuple[float, Deal, rt.Verdict]] = []
    for give, get in _fair_deals(state, settings, know, sender, receiver)[:scored]:
        verdict = rt.evaluate(state, settings, know, sender.id, receiver.id, give, get)
        shared = min(verdict.your_gain, verdict.their_gain)
        if verdict.accepted and (verdict.their_gain if sender.is_user else shared) >= MIN_CPU_GAIN:
            found.append((shared, (give, get), verdict))
    for _, (give, get), verdict in sorted(found, key=lambda row: -row[0]):
        sides = ((sender, give, get, verdict.your_drops), (receiver, get, give, verdict.their_drops))
        if not any(rt.strands_a_starter(settings, know, *side) for side in sides if not side[0].is_user):
            return give, get
    return None


def _free_cpus(state: State) -> list[Team]:
    return [team for team in state.teams if not team.is_user and not rs.has_traded_this_week(state, team.id)]


def _cpu_attempt(state: State, settings: Settings, know: Knowledge, dice: np.random.Generator) -> State:
    """Up to MAX_PAIRS seeded pairs of CPUs that have not traded this week: the first with a deal makes it."""
    pairs = list(combinations(_free_cpus(state), 2))
    for index in dice.permutation(len(pairs))[:MAX_PAIRS]:
        first, second = pairs[int(index)]
        deal = _best_deal(state, settings, know, first, second, PAIR_SCORED)
        if deal is not None:
            return rt.execute(state, settings, know, first.id, second.id, *deal)
    return state


def _user_offer(state: State, settings: Settings, know: Knowledge, dice: np.random.Generator) -> State:
    """At most one new offer to the user a week: `give` is what the CPU sends."""
    roll, free = dice.random(), _free_cpus(state)
    user = next((team for team in state.teams if team.is_user), None)
    if user is None or not free or roll >= USER_OFFER_CHANCE:
        return state
    if any(offer.week == state.week and offer.to_team == user.id for offer in state.offers):
        return state
    partner = free[int(dice.integers(len(free)))]
    deal = _best_deal(state, settings, know, user, partner)
    return state if deal is None else rt.record_offer(state, partner.id, user.id, deal[1], deal[0], "pending")


def cpu_trades(state: State, settings: Settings, know: Knowledge, seed: int) -> State:
    """This week's CPU-CPU trades (carried out) and at most one new pending offer to the user.

    Deterministic for a seed, week and state. Does nothing outside the trading phases or after the deadline."""
    if state.phase not in rt.TRADE_PHASES or state.week > settings.trade_deadline_week:
        return state
    for attempt in range(CPU_ATTEMPTS):
        dice = rs.rng(seed, "trades", state.week, attempt)
        if attempt == 0 or dice.random() < EXTRA_ATTEMPT_CHANCE:
            state = _cpu_attempt(state, settings, know, dice)
    return _user_offer(state, settings, know, rs.rng(seed, "offer", state.week))
