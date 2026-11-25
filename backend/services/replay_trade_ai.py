"""Replay League: the trades CPU managers make on their own, once a week.

`cpu_trades` makes up to CPU_ATTEMPTS attempts at a CPU-CPU deal (the first always, each later one with
probability EXTRA_ATTEMPT_CHANCE) and then at most one CPU sends the user an offer: the free CPUs, in seeded
order, each roll their persona's `replay_personas.offer_rate`, and the first that hits searches for one (a
shark rolls highest). An attempt asks up to MAX_PAIRS seeded pairs of CPUs below the weekly cap
(`replay_state.MAX_TRADES_PER_WEEK`) and makes the first pair's deal that it finds, so a CPU may deal in more
than one attempt; the re-trade lock keeps it from moving the same players again. Each pair is searched within fixed bounds:

1. Each side's TOP_SKILL tradable skill players (out players too, from the active roster), most valuable
   first; a shark looks first at the other side's slumping stars (star x a negative form_chart: buy low)
   and offers its own hot players (sell high); a stasher looks first at the other side's out stars with a
   return date (healthy_chart). Packages of one or two of them. A fan never receives his rival's players.
2. Keep the deals that pass every CPU's market guard on package chart sums (cheap: no lineup maths) and,
   for an offer to the user, need no drop from him and look right for him: a shop's is fair for him
   (`fair_for_user`); one a CPU makes because it wants his player gives him more than he sends, by its
   persona's `replay_personas.offer_edge` (5-10%: `generous`, within EDGE_SLACK on package sums).
3. Run the full `replay_trades.evaluate` on the PAIR_SCORED (MAX_SCORED for the user) closest to the gap
   wanted (none between CPUs, the persona's edge for a CPU's offer) and take the accepted one with the best
   `Verdict.score` (the lowest CPU net in the deal); two CPUs only make a deal that nets both at least
   SEEK_SCORE. A CPU's own offer is evaluated as one (`offered`, at `replay_trades.offer_multiple`), as
   `replay_trades.respond` re-checks it, and must be `generous` on the verdict's values. A dealer looks at
   2-for-2 swaps first.
A dealer also makes DEALER_ATTEMPTS extra CPU-CPU attempts a week (each with EXTRA_ATTEMPT_CHANCE), only with
pairs that include him. Offer rates and edges come from the state (`replay_personas.edge`: a tilted manager on a
skid offers more often and gives more; a haggler's opening offer is a lowball, `haggle_deal` his counter).
Package sums count a player his team picked up this week at his trade discount (`replay_trade_value.fresh`).

A CPU shopping a player to the user (`State.shops`, replay_mood) offers first: the best deal that includes
that player, at his shop discount (no roll), once per shop: a player he has offered the user since the shop
opened (`Shop.opened`; an offer of his to the user with him in `give`) is not offered again while it stays
open, though the user may still propose for him at the discount; a shop that closes and later reopens sends
a new offer. Otherwise the persona rolls above decide. While his shop is open
a shopped player is the user's to buy: CPU-CPU deals never move him, though they run first.
An offer to the user is stored as a pending Offer; nothing changes hands until `replay_trades.respond`.
"""
from collections.abc import Sequence
from itertools import combinations

import numpy as np

from services import replay_fans, replay_personas, replay_state as rs, replay_trade_value as tv, replay_trades as rt
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
EDGE_SLACK = 0.04                # package sums leave out the star premium: look a little wider before evaluating
SEEK_SCORE = 20.0               # chart points both CPUs must net before they go looking for a deal (they accept
                                # one at 0, but don't chase it: at 0 the fake league traded ~15 times a season)
DEALER_ATTEMPTS = 1             # extra CPU-CPU attempts a week per dealer (the user's "more attempts")
Edge = tuple[float, float]      # (least, most) extra share a CPU's own offer gives the user

Deal = tuple[tuple[int, ...], tuple[int, ...]]      # (what the first team sends, what it receives)
Package = tuple[float, tuple[int, ...]]             # (chart sum, ids)


def _order(value: ReplayValue, look: str) -> tuple:
    """Search order: most valuable first; a shark buying looks at slumping stars first (star x a negative
    form_chart), a shark selling offers its hot players (a positive form_chart) first, a stasher buying looks at
    out stars with a return date first (by healthy chart)."""
    first = {"buy": value.star * max(0.0, -value.form_chart), "sell": max(0.0, value.form_chart),
             "stash": value.healthy_chart if replay_personas.stashable(value) else 0.0}.get(look, 0.0)
    return -first, -value.chart, value.player_id


def _tradable(state: State, know: Knowledge, team: Team, look: str = "",
              held_back: frozenset[int] = frozenset()) -> list[ReplayValue]:
    """The team's TOP_SKILL skill players that may move (not re-trade locked, not `held_back`; out players may:
    an IR slot is not on the roster), in search order."""
    values = [value for pid in team.roster if (value := know.by_id[pid]).position in SKILL
              and not rs.retrade_locked(state, pid) and pid not in held_back]
    return sorted(values, key=lambda v: _order(v, look))[:TOP_SKILL]


def _packages(know: Knowledge, values: Sequence[ReplayValue], just_added: frozenset[int]) -> list[Package]:
    return [(sum(tv.chart(know, v.player_id, just_added) for v in combo), tuple(v.player_id for v in combo))
            for size in PACKAGE_SIZES for combo in combinations(values, size)]


def fair_for_user(settings: Settings, know: Knowledge, value_give: float, value_get: float) -> bool:
    """Is a CPU's offer fair for the user, who sends `value_give`? Yes when trade.verdict_for calls it fair
    or better once the premium a CPU asks of the user (none on normal, 8% on hard) is set aside."""
    asked = rs.USER_TRADE_MULTIPLE[settings.difficulty]
    return not rt.fair_label(value_give, value_get * asked).endswith("loss")


def generous(edge: Edge, value_give: float, value_get: float, slack: float = 0.0) -> bool:
    """Does a CPU's offer give the user (who sends `value_give`) its edge (`replay_personas.edge`) more than he
    sends? A haggler's opening edge is below 0: then it gives him less, by that share."""
    least, most = edge
    return value_give > 0 and least - slack <= value_get / value_give - 1.0 <= most + slack


def _hated(know: Knowledge, team: Team, ids: tuple[int, ...]) -> bool:
    return any(replay_fans.banned(team, know.by_id[pid].team) for pid in ids)


def _candidates(state: State, settings: Settings, know: Knowledge, sender: Team, receiver: Team,
                want: Edge | None = None) -> list[Deal]:
    """Deals that pass the cheap checks, closest to the gap wanted first (`want`: the receiver's own offer to
    the user, the sender, for his player, at that edge; a dealer's 2-for-2 swaps first). `receiver` is a CPU."""
    def look(team: Team, other: Team) -> str:
        if team.persona == "shark" and not team.is_user:
            return "sell"
        return "buy" if other.persona == "shark" else "stash" if other.persona == "stasher" else ""

    just_added = tv.fresh(know, rs.pickups(state))
    shopped = frozenset() if sender.is_user else frozenset(shop.player_id for shop in state.shops)   # the user's to buy
    mine = _packages(know, _tradable(state, know, sender, look(sender, receiver), shopped), just_added)
    theirs = _packages(know, _tradable(state, know, receiver, look(receiver, sender), shopped), just_added)
    edge = sum(want) / 2 if want else 0.0
    swaps = receiver.persona == "dealer" or sender.persona == "dealer"
    found = []
    for give in mine:
        if _hated(know, receiver, give[1]):
            continue
        for get in theirs:
            if give[0] < tv.MARKET_GUARD * get[0] or _hated(know, sender, get[1]):    # the receiving CPU's guard
                continue
            if sender.is_user:
                fits = len(sender.roster) - len(give[1]) + len(get[1]) <= settings.roster_size
                looks_right = (generous(want, give[0], get[0], EDGE_SLACK) if want
                               else fair_for_user(settings, know, give[0], get[0]))
                if not fits or not looks_right:
                    continue
            elif get[0] < tv.MARKET_GUARD * give[0]:
                continue
            size = len(give[1]) + len(get[1])
            found.append((swaps and size != 4, abs(give[0] * (1 + edge) - get[0]), size, give[1], get[1]))
    return [(give, get) for *_, give, get in sorted(found)]


def _best_deal(state: State, settings: Settings, know: Knowledge, sender: Team, receiver: Team,
               scored: int = MAX_SCORED, need: int | None = None, want: bool = False, counter: bool = False,
               wanted: int | None = None) -> Deal | None:
    """The accepted deal with the best score among the `scored` candidates closest to the gap wanted (only
    those where the receiver sends `need`, and where the sender sends `wanted`, when given). `want`: the
    receiver's own offer for the user's player, judged as one and generous to him (`counter`: one that answers
    another, at that edge)."""
    best: tuple[float, Deal] | None = None
    edge = replay_personas.edge(state, receiver, counter) if want else None
    found = _candidates(state, settings, know, sender, receiver, edge)
    for give, get in [deal for deal in found if (need is None or need in deal[1])
                      and (wanted is None or wanted in deal[0])][:scored]:
        verdict = rt.evaluate(state, settings, know, sender.id, receiver.id, give, get, offered=want, counter=counter)
        if not verdict.accepted or (not sender.is_user and verdict.score < SEEK_SCORE):
            continue
        if sender.is_user and not (generous(edge, verdict.value_give, verdict.value_get) if edge
                                   else fair_for_user(settings, know, verdict.value_give, verdict.value_get)):
            continue
        if best is None or verdict.score > best[0]:
            best = (verdict.score, (give, get))
    return None if best is None else best[1]


def _free_cpus(state: State) -> list[Team]:
    """CPUs below the weekly trade cap."""
    return [team for team in state.teams
            if not team.is_user and rs.trades_this_week(state, team.id) < rs.MAX_TRADES_PER_WEEK]


def _cpu_attempt(state: State, settings: Settings, know: Knowledge, dice: np.random.Generator,
                 with_team: int | None = None) -> State:
    """Up to MAX_PAIRS seeded pairs of CPUs below the weekly trade cap (only those with `with_team`, when
    given): the first with a deal makes it."""
    pairs = [pair for pair in combinations(_free_cpus(state), 2)
             if with_team is None or with_team in (pair[0].id, pair[1].id)]
    for index in dice.permutation(len(pairs))[:MAX_PAIRS]:
        first, second = pairs[int(index)]
        deal = _best_deal(state, settings, know, first, second, PAIR_SCORED)
        if deal is not None:
            return rt.execute(state, settings, know, first.id, second.id, *deal)
    return state


def _offered_since(state: State, team_id: int, user_id: int, player_id: int, week: int) -> bool:
    """Has this CPU offered the user that player since `week` (this shop's opening week)?"""
    return any(offer.from_team == team_id and offer.to_team == user_id and offer.week >= week
               and player_id in offer.give for offer in state.offers)


def _user_offer(state: State, settings: Settings, know: Knowledge, dice: np.random.Generator) -> State:
    """At most one new offer to the user a week, from the first free CPU whose offer rate hits."""
    user, free = next((team for team in state.teams if team.is_user), None), _free_cpus(state)
    if user is None or any(offer.week == state.week and offer.to_team == user.id for offer in state.offers):
        return state
    for team in free:                               # a shopping CPU offers his player first, once per shop
        live = rs.markdowns(state, team.id)
        for pid in [shop.player_id for shop in state.shops if shop.team_id == team.id and shop.player_id in live
                    and not _offered_since(state, team.id, user.id, shop.player_id, shop.opened)]:
            deal = _best_deal(state, settings, know, user, team, need=pid)
            if deal is not None:
                return rt.record_offer(state, team.id, user.id, deal[1], deal[0], "pending")
    rolls = dice.random(len(free))
    order = dice.permutation(len(free))

    def rate(team: Team) -> float:
        return replay_personas.offer_rate(team.persona, rs.losses_in_a_row(state, team.id))

    partner = next((free[int(i)] for i in order if rolls[int(i)] < rate(free[int(i)])), None)
    if partner is None:
        return state
    deal = _best_deal(state, settings, know, user, partner, want=True)
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
    for dealer in [team.id for team in state.teams if team.persona == "dealer" and not team.is_user]:
        for attempt in range(DEALER_ATTEMPTS):
            dice = rs.rng(seed, "dealer-trades", state.week, dealer, attempt)
            if dice.random() < EXTRA_ATTEMPT_CHANCE:
                state = _cpu_attempt(state, settings, know, dice, dealer)
    return _user_offer(state, settings, know, rs.rng(seed, "offer", state.week))


def haggle_deal(state: State, settings: Settings, know: Knowledge, offer_from: Team, user_sends: tuple[int, ...]) -> Deal | None:
    """A haggler's counter after the user declines his opening offer: the best deal at his normal edge
    (`replay_personas.HAGGLE_COUNTER_EDGE`) that still gets him the best of the user's players he asked for
    (`user_sends`), or None."""
    user = rs.user_team(state)
    held = [pid for pid in user_sends if pid in user.roster]
    if not held:
        return None
    best = max(held, key=lambda pid: (know.by_id[pid].chart, -pid))
    return _best_deal(state, settings, know, user, offer_from, want=True, counter=True, wanted=best)
