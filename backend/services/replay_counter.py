"""Replay League counter-offers: after a value-only decline a CPU may answer with a deal it would take.

It counters with probability `replay_personas.counter_rate`, one roll per CPU per week (so declining and
re-proposing can't reroll it), and searches at most once per week (`State.counter_searched`), so at most one
counter a week and repeated declines never rerun the search. Candidates: add one of the user's players, swap a
requested player for a lesser one at his position, or drop one requested player; it picks the one it accepts
that costs the user least (never a "big_loss" for the user, never one needing a user drop) and stores it as a
pending Offer(counter_to=declined.id). The declined deal is re-checked with the user's own drops. It never
counters a counter of its own counter: the user can only answer a counter in the week it was made, and a CPU
counters at most once a week.

`haggle_counter`: a haggler always comes back when the user declines his opening offer, at his normal edge.
"""
from collections.abc import Iterator, Sequence
from dataclasses import replace

from services import replay_personas, replay_state as rs, replay_trade_ai, replay_trades as rt
from services.replay_state import Offer, Settings, State, Team
from services.replay_values import Knowledge

Deal = tuple[tuple[int, ...], tuple[int, ...]]      # (the user sends, the user gets)


def _candidates(state: State, know: Knowledge, owner: Team, give: tuple[int, ...], get: tuple[int, ...]) -> Iterator[Deal]:
    """`owner`: the CPU asked for `get` (its players may sit in its IR slot: rs.held)."""
    user = rs.user_team(state)
    if len(give) < rt.MAX_PLAYERS_PER_SIDE:
        yield from ((give + (pid,), get) for pid in user.roster if pid not in give)
    for asked in get:
        wanted = know.by_id[asked]
        yield from ((give, tuple(pid if p == asked else p for p in get)) for pid in rs.held(owner)
                    if pid not in get and know.by_id[pid].position == wanted.position
                    and know.by_id[pid].chart < wanted.chart)
        if len(get) > 1:
            yield give, tuple(p for p in get if p != asked)


def _countered_this_week(state: State, cpu_id: int) -> bool:
    return (state.counter_searched.get(cpu_id) == state.week
            or any(o.from_team == cpu_id and o.week == state.week and o.counter_to is not None for o in state.offers))


def counter_offer(state: State, settings: Settings, know: Knowledge, declined: Offer, seed: int,
                  user_drops: Sequence[int] = ()) -> State:
    """The state with the CPU's counter to the user's declined proposal added as a pending Offer, or unchanged."""
    cpu = rs.team(state, declined.to_team)
    if (declined.status != "declined" or cpu.is_user or declined.from_team != rs.user_team(state).id
            or _countered_this_week(state, cpu.id)):
        return state
    try:
        verdict = rt.evaluate(state, settings, know, declined.from_team, cpu.id, declined.give, declined.get,
                              user_drops)
    except ValueError:
        return state
    if verdict.accepted or not all(reason.startswith(rt.VALUE_REASON) for reason in verdict.reasons):
        return state
    if rs.rng(seed, "counter", state.week, cpu.id).random() >= replay_personas.counter_rate(cpu.persona):
        return state
    state = replace(state, counter_searched={**state.counter_searched, cpu.id: state.week})
    best: tuple[float, Deal] | None = None
    for give, get in _candidates(state, know, cpu, declined.give, declined.get):
        try:
            found = rt.evaluate(state, settings, know, declined.from_team, cpu.id, give, get)
        except ValueError:                       # e.g. it would need the user to drop someone
            continue
        if not found.accepted or found.fair_label == "big_loss":
            continue
        cost = found.value_give - found.value_get
        if best is None or cost < best[0]:
            best = (cost, (give, get))
    if best is None:
        return state
    give, get = best[1]
    return rt.record_offer(state, cpu.id, declined.from_team, get, give, "pending", declined.id)


def haggle_counter(state: State, settings: Settings, know: Knowledge, declined: Offer) -> State:
    """A haggler always answers the user declining his opening offer (`declined`, his own, no counter itself)
    with a counter at his normal edge for the same players of the user's (replay_trade_ai.haggle_deal), as a
    pending Offer(counter_to=declined.id). Unchanged for anyone else, or when no such deal stands now."""
    cpu, user = rs.team(state, declined.from_team), rs.user_team(state)
    if (cpu.is_user or cpu.persona != "haggler" or declined.to_team != user.id or declined.status != "declined"
            or declined.counter_to is not None or declined.week != state.week
            or any(o.counter_to == declined.id for o in state.offers)):
        return state
    deal = replay_trade_ai.haggle_deal(state, settings, know, cpu, declined.get)
    if deal is None:
        return state
    return rt.record_offer(state, cpu.id, user.id, deal[1], deal[0], "pending", declined.id)
