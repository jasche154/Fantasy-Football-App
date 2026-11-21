"""Replay League counter-offers (services/replay_counter.py), on the plain chart from test_replay_trades."""
from dataclasses import replace

import pytest

from services import replay_counter as rc, replay_state as rs, replay_trades as rt
from tests import replay_fakes as fakes
from tests.test_replay_trades import league

P = fakes.player_id
SETTINGS = fakes.fake_settings()
USER, CPU, NEEDS_RB = 1, 3, 9
SCRUB = P("WR", 54)
SHORT = (CPU, [P("RB", 14)], [P("RB", 17)])     # a little too little for CPU 3's back


@pytest.fixture
def always(monkeypatch):
    monkeypatch.setattr(rc.replay_personas, "counter_rate", lambda persona: 1.0)


def declined(state=None, deal=SHORT, counter_to=None):
    base, know = league()
    after, verdict = rt.propose(state or base, SETTINGS, know, *deal, counter_to=counter_to)
    assert not verdict.accepted
    return after, know, after.offers[-1]


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
    assert rc.counter_offer(later, SETTINGS, know, mine, seed=0) is later       # values are for week 1: no deal
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
