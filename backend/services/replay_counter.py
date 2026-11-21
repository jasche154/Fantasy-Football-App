"""Replay League counter-offers: after a value-only decline a CPU may answer with a deal it would take.

It counters with probability `replay_personas.counter_rate`, one roll per CPU per week (so declining and
re-proposing can't reroll it), and at most once per week. Candidates: add one of the user's players, swap a
requested player for a lesser one at his position, or drop one requested player; it picks the one it accepts
that costs the user least (never a "big_loss" for the user, never one needing a user drop) and stores it as a
pending Offer(counter_to=declined.id). It never counters a counter of its own counter. See .plans/pr1-plan.md
section D.
"""
from collections.abc import Iterator

from services import replay_personas, replay_state as rs, replay_trades as rt
from services.replay_state import Offer, Settings, State
from services.replay_values import Knowledge

Deal = tuple[tuple[int, ...], tuple[int, ...]]      # (the user sends, the user gets)


def _candidates(state: State, know: Knowledge, give: tuple[int, ...], get: tuple[int, ...]) -> Iterator[Deal]:
    user = rs.user_team(state)
    if len(give) < rt.MAX_PLAYERS_PER_SIDE:
        yield from ((give + (pid,), get) for pid in user.roster if pid not in give)
    owner = next(t for t in state.teams if get[0] in t.roster)
    for asked in get:
        wanted = know.by_id[asked]
        yield from ((give, tuple(pid if p == asked else p for p in get)) for pid in owner.roster
                    if pid not in get and know.by_id[pid].position == wanted.position
                    and know.by_id[pid].chart < wanted.chart)
        if len(get) > 1:
            yield give, tuple(p for p in get if p != asked)


def _own_counter_answered(state: State, declined: Offer) -> bool:
    """Is `declined` the user's counter to an offer that was itself this CPU's counter?"""
    answered = next((o for o in state.offers if o.id == declined.counter_to), None)
    return answered is not None and answered.from_team == declined.to_team and answered.counter_to is not None


def _countered_this_week(state: State, cpu_id: int) -> bool:
    return any(o.from_team == cpu_id and o.week == state.week and o.counter_to is not None for o in state.offers)


def counter_offer(state: State, settings: Settings, know: Knowledge, declined: Offer, seed: int) -> State:
    """The state with the CPU's counter to the user's declined proposal added as a pending Offer, or unchanged."""
    cpu = rs.team(state, declined.to_team)
    if (declined.status != "declined" or cpu.is_user or declined.from_team != rs.user_team(state).id
            or _own_counter_answered(state, declined) or _countered_this_week(state, cpu.id)):
        return state
    try:
        verdict = rt.evaluate(state, settings, know, declined.from_team, cpu.id, declined.give, declined.get)
    except ValueError:
        return state
    if verdict.accepted or not all(reason.startswith(rt.VALUE_REASON) for reason in verdict.reasons):
        return state
    if rs.rng(seed, "counter", state.week, cpu.id).random() >= replay_personas.counter_rate(cpu.persona):
        return state
    best: tuple[float, Deal] | None = None
    for give, get in _candidates(state, know, declined.give, declined.get):
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
