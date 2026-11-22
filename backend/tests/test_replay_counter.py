"""Replay League counter-offers (services/replay_counter.py), on the plain chart from test_replay_trades."""
from dataclasses import replace

import pytest

from services import replay_counter as rc, replay_state as rs, replay_trades as rt
from tests import replay_fakes as fakes
from tests.test_replay_trades import chart_knowledge, league, star, with_persona

P = fakes.player_id
SETTINGS = fakes.fake_settings()
USER, CPU, NEEDS_RB = 1, 3, 9
SCRUB = P("WR", 54)
RB14, RB17, RB28, RB31, WR42 = P("RB", 14), P("RB", 17), P("RB", 28), P("RB", 31), P("WR", 42)
SHORT = (CPU, [RB14], [RB17])     # a little too little for CPU 3's back


@pytest.fixture
def always(monkeypatch):
    monkeypatch.setattr(rc.replay_personas, "counter_rate", lambda persona: 1.0)


def declined(state=None, deal=SHORT, counter_to=None, changes=None, settings=SETTINGS, drops=()):
    base, know = league(settings=settings, changes=changes)
    after, verdict = rt.propose(state or base, settings, know, *deal, drops, counter_to=counter_to)
    assert not verdict.accepted
    return after, know, after.offers[-1]


def countered(changes=None, deal=SHORT, settings=SETTINGS, drops=(), state=None):
    """(user sends, user gets) in the CPU's counter to `deal`, or None."""
    after, know, mine = declined(state, deal, changes=changes, settings=settings, drops=drops)
    answer = rc.counter_offer(after, settings, know, mine, 0, drops)
    return (answer.offers[-1].get, answer.offers[-1].give) if len(answer.offers) > len(after.offers) else None


def test_a_value_decline_gets_the_cheapest_counter_the_cpu_would_take(always):
    state, know, mine = declined()

    after = rc.counter_offer(state, SETTINGS, know, mine, seed=0)

    counter = after.offers[-1]
    assert (counter.from_team, counter.to_team, counter.status, counter.counter_to) == (CPU, USER, "pending",
                                                                                        mine.id)
    verdict = rt.evaluate(after, SETTINGS, know, USER, CPU, counter.get, counter.give)
    assert verdict.accepted and verdict.fair_label != "big_loss" and verdict.your_drops == ()
    assert rt.offers_for_user(after) == (counter,) and after.teams == state.teams
    accepted = rt.respond(after, SETTINGS, know, counter.id, accept=True)
    assert accepted.offers[-1].status == "accepted"


def test_it_counters_once_and_only_as_often_as_its_persona_does(always, monkeypatch):
    state, know, mine = declined()
    once = rc.counter_offer(state, SETTINGS, know, mine, seed=0)

    assert rc.counter_offer(once, SETTINGS, know, mine, seed=0) is once
    monkeypatch.setattr(rc.replay_personas, "counter_rate", lambda persona: 0.0)
    assert rc.counter_offer(state, SETTINGS, know, mine, seed=0) is state


def test_no_counter_when_value_was_not_the_only_problem(always):
    base, know = league()
    traded, _ = rt.propose(base, SETTINGS, know, NEEDS_RB, [P("RB", 14)], [P("TE", 7)])
    state, _, mine = declined(traded, (NEEDS_RB, [SCRUB], [P("TE", 6)]))

    assert any("already made a trade" in reason for reason in rt.evaluate(
        state, SETTINGS, know, USER, NEEDS_RB, mine.give, mine.get).reasons)
    assert rc.counter_offer(state, SETTINGS, know, mine, seed=0) is state


def test_it_never_counters_a_counter_of_its_own_counter(always):
    state, know, mine = declined()
    countered = rc.counter_offer(state, SETTINGS, know, mine, seed=0)
    theirs = countered.offers[-1]

    answered, know2, again = declined(countered, SHORT, counter_to=theirs.id)
    original = rt.record_offer(league()[0], CPU, USER, (P("RB", 17),), (P("RB", 14), P("RB", 23)), "pending")
    to_original, _, reply = declined(original, SHORT, counter_to=original.offers[-1].id)

    assert answered.offers[-2].status == "countered" and again.counter_to == theirs.id
    assert rc.counter_offer(answered, SETTINGS, know2, again, seed=0) is answered
    assert rc.counter_offer(to_original, SETTINGS, know2, reply, seed=0).offers[-1].counter_to == reply.id


def test_only_the_users_declined_proposals_are_countered(always):
    state, know, mine = declined()

    assert rc.counter_offer(state, SETTINGS, know, replace(mine, status="accepted"), seed=0) is state
    later = replace(state, week=2)
    assert rc.counter_offer(later, SETTINGS, know, mine, seed=0).offers == later.offers   # week-1 values: no deal
    assert rs.user_team(state).id == mine.from_team


def test_one_roll_per_cpu_per_week_and_at_most_one_counter(always, monkeypatch):
    state, know, mine = declined()
    once = rc.counter_offer(state, SETTINGS, know, mine, seed=0)
    again, _, other = declined(once, (CPU, [P("RB", 23)], [P("RB", 17)]))
    rolls, roll = [], rs.rng
    monkeypatch.setattr(rs, "rng", lambda *parts: (rolls.append(parts), roll(*parts))[1])

    rc.counter_offer(state, SETTINGS, know, mine, seed=7)

    assert rolls == [(7, "counter", state.week, CPU)]                  # not the offer id: re-proposing can't reroll
    assert once.offers[-1].counter_to == mine.id
    assert rc.counter_offer(again, SETTINGS, know, other, seed=0) is again      # already countered this week


def test_a_search_that_finds_nothing_is_not_rerun_that_week(always, monkeypatch):
    state, know, mine = declined()
    searches = []
    monkeypatch.setattr(rc, "_candidates", lambda *args: searches.append(args) or iter(()))

    first = rc.counter_offer(state, SETTINGS, know, mine, seed=0)
    again, _, other = declined(first, (CPU, [P("RB", 23)], [P("RB", 17)]))
    second = rc.counter_offer(again, SETTINGS, know, other, seed=0)

    assert len(searches) == 1 and first.offers == state.offers and second is again
    assert first.counter_searched == {CPU: state.week}
    assert rs.state_from_json(rs.to_json(first)) == first
    next_week = replace(again, week=state.week + 1)
    assert rc._countered_this_week(next_week, CPU) is False


# ---------------- which counter: each generator, the cheapest, difficulty, the market guard ----------------

def test_the_add_generator_asks_for_the_users_cheapest_extra_player(always):
    assert countered() == ((RB14, WR42), (RB17,))


def test_the_swap_generator_offers_a_lesser_back_and_beats_a_dearer_add(always):
    """Two counters qualify: RB14 + WR42 for RB17 (costs the user ~26) and RB14 for RB28 (~18). max() would
    pick the first."""
    changes = {RB28: star(380.0)}
    state, know, mine = declined(changes=changes)
    for give, get in (((RB14, WR42), (RB17,)), ((RB14,), (RB28,))):
        assert rt.evaluate(state, SETTINGS, know, USER, CPU, give, get).accepted

    assert countered(changes) == ((RB14,), (RB28,))


def test_the_drop_generator_drops_a_requested_player_and_the_users_drops_count(always):
    """RB14 for RB17 + RB28 leaves the user one over the limit: the decline is re-checked with his drop."""
    changes, deal = {RB14: star(450.0)}, (CPU, [RB14], [RB17, RB28])
    state, know, mine = declined(deal=deal, changes=changes, drops=(SCRUB,))
    assert rt.evaluate(state, SETTINGS, know, USER, CPU, (RB14, RB31), (RB17, RB28)).accepted    # dearer for him

    assert countered(changes, deal, drops=(SCRUB,)) == ((RB14,), (RB17,))
    assert rc.counter_offer(state, SETTINGS, know, mine, 0) is state            # without his drop: no counter


def test_on_hard_the_cpu_wants_more_so_the_counter_changes(always):
    changes, hard = {RB28: star(345.0)}, replace(SETTINGS, difficulty="hard")

    assert countered(changes) == ((RB14, WR42), (RB17,))
    assert countered(changes, settings=hard) == ((RB14,), (RB28,))
    assert countered(settings=hard) is None                                      # nothing it takes is cheap enough


def test_a_decline_by_the_market_guard_alone_gets_a_counter_that_passes_it(always):
    """A steady CPU rates the user's back (drafted high) above its own bench back, but 330 for 430 is
    under 80% back on the chart."""
    changes = {RB14: star(330.0, star=1.0, pre_chart=900.0), RB28: star(430.0, star=1.0, pre_chart=0.0)}
    base = with_persona(league(changes=changes)[0], CPU, "steady")
    state, know, mine = declined(base, (CPU, [RB14], [RB28]), changes=changes)
    reasons = rt.evaluate(state, SETTINGS, know, USER, CPU, mine.give, mine.get).reasons

    assert reasons == ("Value is too far apart: CPU 3 won't take back less than 80% of what it gives.",)
    assert countered(changes, (CPU, [RB14], [RB28]), state=base) == ((RB14, SCRUB), (RB28,))


@pytest.mark.parametrize("week", [1, 9])
def test_the_counter_does_not_change_when_the_future_does(always, week):
    base = replace(fakes.drafted_state(), week=week)
    real = chart_knowledge(week)
    changed = chart_knowledge(week, data=fakes.future_changed(fakes.fake_season(), after_week=week))
    state, _ = rt.propose(base, SETTINGS, real, *SHORT)
    mine = state.offers[-1]

    answer = rc.counter_offer(state, SETTINGS, real, mine, seed=0)
    assert len(answer.offers) == len(state.offers) + 1
    assert rc.counter_offer(state, SETTINGS, changed, mine, seed=0) == answer
