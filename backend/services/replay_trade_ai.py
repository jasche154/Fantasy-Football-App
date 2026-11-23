"""Replay League: the trades CPU managers make on their own, once a week.

`cpu_trades` makes up to CPU_ATTEMPTS attempts at a CPU-CPU deal (the first always, each later one with
probability EXTRA_ATTEMPT_CHANCE) and then at most one CPU sends the user an offer: the free CPUs, in seeded
order, each roll their persona's `replay_personas.offer_rate`, and the first that hits searches for one (a
shark rolls highest). An attempt asks up to MAX_PAIRS seeded pairs of CPUs below the weekly cap
(`replay_state.MAX_TRADES_PER_WEEK`) and makes the first pair's deal that it finds, so a CPU may deal in more
than one attempt; the re-trade lock keeps it from moving the same players again. Each pair is searched within fixed bounds:

1. Each side's TOP_SKILL tradable skill players (out players too, from the active roster), most valuable
   first; a shark looks first at the other side's slumping stars (star x a negative form_chart: buy low)
   and offers its own hot players (sell high). Packages of one or two of them.
2. Keep the deals that pass every CPU's market guard on package chart sums (cheap: no lineup maths) and,
   for an offer to the user, are fair for the user (`fair_for_user`) and need no drop from him.
3. Run the full `replay_trades.evaluate` on the PAIR_SCORED (MAX_SCORED for the user) with the smallest
   chart gap and take the accepted one with the best `Verdict.score` (the lowest CPU net in the deal); two
   CPUs only make a deal that nets both at least SEEK_SCORE.

An offer to the user is stored as a pending Offer; nothing changes hands until `replay_trades.respond`.
"""
from collections.abc import Sequence
from itertools import combinations

import numpy as np

from services import replay_personas, replay_state as rs, replay_trade_value as tv, replay_trades as rt
from services.replay_pool import SKILL
from services.replay_state import Settings, State, Team
from services.replay_values import Knowledge, ReplayValue

CPU_ATTEMPTS = 2
EXTRA_ATTEMPT_CHANCE = 0.5      # each attempt after the first
MAX_PAIRS = 3                   # pairs of CPUs one attempt asks before it gives up
TOP_SKILL = 8                   # players per team the search may move
PACKAGE_SIZES = (1, 2)          # 1-for-1, 2-for-1, 1-for-2 and 2-for-2
MAX_SCORED = 12                 # deals that get the full evaluation for an offer to the user
PAIR_SCORED = 8                 # and for each pair of CPUs
SEEK_SCORE = 20.0               # chart points both CPUs must net before they go looking for a deal (they accept
                                # one at 0, but don't chase it: at 0 the fake league traded ~15 times a season)

Deal = tuple[tuple[int, ...], tuple[int, ...]]      # (what the first team sends, what it receives)
Package = tuple[float, tuple[int, ...]]             # (chart sum, ids)


def _order(value: ReplayValue, look: str) -> tuple:
    """Search order: most valuable first; a shark buying looks at slumping stars first (star x a negative
    form_chart), a shark selling offers its hot players (a positive form_chart) first."""
    first = {"buy": value.star * max(0.0, -value.form_chart), "sell": max(0.0, value.form_chart)}.get(look, 0.0)
    return -first, -value.chart, value.player_id


def _tradable(state: State, know: Knowledge, team: Team, look: str = "") -> list[ReplayValue]:
    """The team's TOP_SKILL skill players that may move (not re-trade locked; out players may: an IR slot is
    not on the roster), in search order."""
    values = [value for pid in team.roster if (value := know.by_id[pid]).position in SKILL
              and not rs.retrade_locked(state, pid)]
    return sorted(values, key=lambda v: _order(v, look))[:TOP_SKILL]


def _packages(values: Sequence[ReplayValue]) -> list[Package]:
    return [(sum(v.chart for v in combo), tuple(v.player_id for v in combo))
            for size in PACKAGE_SIZES for combo in combinations(values, size)]


def fair_for_user(settings: Settings, know: Knowledge, value_give: float, value_get: float) -> bool:
    """Is a CPU's offer fair for the user, who sends `value_give`? Yes when trade.verdict_for calls it fair
    or better once the premium a CPU asks of the user (none on normal, 8% on hard) is set aside."""
    asked = rs.USER_TRADE_MULTIPLE[settings.difficulty]
    return not rt.fair_label(value_give, value_get * asked).endswith("loss")


def _candidates(state: State, settings: Settings, know: Knowledge, sender: Team, receiver: Team) -> list[Deal]:
    """Deals that pass the cheap checks, smallest chart gap first. `receiver` is a CPU."""
    def look(team: Team, other: Team) -> str:
        return "sell" if team.persona == "shark" and not team.is_user else "buy" if other.persona == "shark" else ""

    mine = _packages(_tradable(state, know, sender, look(sender, receiver)))
    theirs = _packages(_tradable(state, know, receiver, look(receiver, sender)))
    found = []
    for give in mine:
        for get in theirs:
            if give[0] < tv.MARKET_GUARD * get[0]:                       # the receiving CPU's guard
                continue
            if sender.is_user:
                fits = len(sender.roster) - len(give[1]) + len(get[1]) <= settings.roster_size
                if not fits or not fair_for_user(settings, know, give[0], get[0]):
                    continue
            elif get[0] < tv.MARKET_GUARD * give[0]:
                continue
            found.append((abs(give[0] - get[0]), len(give[1]) + len(get[1]), give[1], get[1]))
    return [(give, get) for _, _, give, get in sorted(found)]


def _best_deal(state: State, settings: Settings, know: Knowledge, sender: Team, receiver: Team,
               scored: int = MAX_SCORED) -> Deal | None:
    """The accepted deal with the best score among the `scored` candidates with the smallest gap."""
    best: tuple[float, Deal] | None = None
    for give, get in _candidates(state, settings, know, sender, receiver)[:scored]:
        verdict = rt.evaluate(state, settings, know, sender.id, receiver.id, give, get)
        if not verdict.accepted or (not sender.is_user and verdict.score < SEEK_SCORE):
            continue
        if sender.is_user and not fair_for_user(settings, know, verdict.value_give, verdict.value_get):
            continue
        if best is None or verdict.score > best[0]:
            best = (verdict.score, (give, get))
    return None if best is None else best[1]


def _free_cpus(state: State) -> list[Team]:
    """CPUs below the weekly trade cap."""
    return [team for team in state.teams
            if not team.is_user and rs.trades_this_week(state, team.id) < rs.MAX_TRADES_PER_WEEK]


def _cpu_attempt(state: State, settings: Settings, know: Knowledge, dice: np.random.Generator) -> State:
    """Up to MAX_PAIRS seeded pairs of CPUs below the weekly trade cap: the first with a deal makes it."""
    pairs = list(combinations(_free_cpus(state), 2))
    for index in dice.permutation(len(pairs))[:MAX_PAIRS]:
        first, second = pairs[int(index)]
        deal = _best_deal(state, settings, know, first, second, PAIR_SCORED)
        if deal is not None:
            return rt.execute(state, settings, know, first.id, second.id, *deal)
    return state


def _user_offer(state: State, settings: Settings, know: Knowledge, dice: np.random.Generator) -> State:
    """At most one new offer to the user a week, from the first free CPU whose offer rate hits."""
    user, free = next((team for team in state.teams if team.is_user), None), _free_cpus(state)
    if user is None or any(offer.week == state.week and offer.to_team == user.id for offer in state.offers):
        return state
    rolls = dice.random(len(free))
    order = dice.permutation(len(free))
    partner = next((free[int(i)] for i in order if rolls[int(i)] < replay_personas.offer_rate(free[int(i)].persona)),
                   None)
    if partner is None:
        return state
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
