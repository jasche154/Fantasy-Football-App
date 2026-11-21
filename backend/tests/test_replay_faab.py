"""Replay League FAAB waivers: the budget in the state, bids on claims, the bid-first waiver run and the week
flow (services/replay_state.py, replay_waivers.py, replay_game.py), on the fake league."""
import json
from dataclasses import replace

import pytest

from services import replay_game as rg, replay_state as rs, replay_waivers as waivers
from services.replay_state import Claim, State
from services.replay_values import Knowledge
from tests import replay_fakes as fakes

FAAB = fakes.fake_settings(waiver_mode="faab")
WEDNESDAY = fakes.fake_settings()
DATA = fakes.fake_season()
USER = FAAB.draft_slot
FIRST, SECOND, THIRD = 12, 11, 10           # the first three teams in the fake league's waiver order
TOO_MUCH = "Enter a bid: $0 to ${} (your budget left)."


def league(settings: rs.Settings = FAAB, phase: str = "waivers", week: int = 1, **spent: int) -> State:
    """The drafted fake league; `spent` gives dollars already spent by team ("t1": 70)."""
    state = replace(fakes.drafted_state(settings), phase=phase, week=week)
    return replace(state, faab_spent={int(key[1:]): value for key, value in spent.items()})


def know(week: int = 1) -> Knowledge:
    return fakes.fake_knowledge(week, FAAB)


def wire(state: State, count: int = 3) -> list[int]:
    return [v.player_id for v in waivers.available_players(state, know(state.week), "WR")][:count]


def cuts(state: State, team_id: int) -> list[int]:
    """That team's wide receivers, worst last (safe drops: every team has spares)."""
    by_id = know(state.week).by_id
    return [pid for pid in rs.team(state, team_id).roster if by_id[pid].position == "WR"][::-1]


def claim(number: int, team_id: int, add: int, drop: int, bid: int) -> Claim:
    return Claim(id=900 + number, team_id=team_id, add=add, drop=drop, bid=bid)


def owner(state: State, player_id: int) -> int | None:
    return next((t.id for t in state.teams if player_id in t.roster), None)


def run(state: State, claims: dict, settings: rs.Settings = FAAB) -> State:
    return waivers.process_claims(state, settings, know(state.week), claims)


# ---------------- state (F1-F6) ----------------

def test_faab_is_a_waiver_mode_with_a_budget_per_team():
    assert rs.WAIVER_MODES == ("wednesday", "faab", "none") and rs.FAAB_BUDGET == 100
    state = league(t3=35)

    assert (rs.faab_left(state, 3), rs.faab_left(state, 4)) == (65, 100)
    assert rs.state_from_json(rs.to_json(state)).faab_spent == {3: 35}


def test_a_game_saved_before_faab_loads_with_no_spending_and_zero_bids_and_plays_on():
    state = league(WEDNESDAY)
    state = waivers.submit_claim(state, WEDNESDAY, know(), wire(state)[0], cuts(state, USER)[0])
    saved = json.loads(rs.to_json(state))
    del saved["faab_spent"]
    for row in saved["claims"]:
        del row["bid"]

    loaded = rs.state_from_json(json.dumps(saved))
    played = rg.advance(rg.advance(loaded, WEDNESDAY, DATA, 1, "wednesday"), WEDNESDAY, DATA, 1, "play")

    assert loaded.faab_spent == {} and loaded.claims[0].bid == 0 and rs.STATE_VERSION == 1
    assert played.week == 2 and played.faab_spent == {}


# ---------------- the user's bids (F7, F8) ----------------

def test_a_faab_claim_needs_a_bid_within_the_budget_left_and_keeps_it():
    state = league(t1=90)
    add, cut = wire(state)[0], cuts(state, USER)[0]

    for bid in (None, -1, 11, True, 2.5):
        with pytest.raises(ValueError) as refused:
            waivers.submit_claim(state, FAAB, know(), add, cut, bid)
        assert str(refused.value) == TOO_MUCH.format(10), bid
    stored = waivers.submit_claim(state, FAAB, know(), add, cut, 10)

    assert stored.claims[0].bid == 10


def test_claims_may_together_bid_more_than_the_budget_left():
    state = league(t1=90)
    first, second = wire(state)[:2]
    state = waivers.submit_claim(state, FAAB, know(), first, cuts(state, USER)[0], 10)

    state = waivers.submit_claim(state, FAAB, know(), second, cuts(state, USER)[1], 10)

    assert [c.bid for c in state.claims] == [10, 10]


def test_a_league_without_faab_refuses_a_bid_and_stores_zero():
    state = league(WEDNESDAY)
    add, cut = wire(state)[0], cuts(state, USER)[0]

    with pytest.raises(ValueError, match="^This league has no FAAB bidding: leave the bid out.$"):
        waivers.submit_claim(state, WEDNESDAY, know(), add, cut, 0)
    assert waivers.submit_claim(state, WEDNESDAY, know(), add, cut).claims[0].bid == 0


def test_a_pending_bid_can_be_changed_before_the_run_and_keeps_its_place():
    state = league(t1=50)
    for n, add in enumerate(wire(state)[:2]):
        state = waivers.submit_claim(state, FAAB, know(), add, cuts(state, USER)[n], 5)
    first, second = (c.id for c in state.claims)

    changed = waivers.set_claim_bid(state, FAAB, first, 50)

    assert [(c.id, c.bid) for c in changed.claims] == [(first, 50), (second, 5)]
    cases = [(replace(state, phase="open"), FAAB, first, 5, "Bids can only be changed before the weekly waiver run."),
             (state, FAAB, 12345, 5, "There is no pending claim 12345."),
             (state, FAAB, first, 51, TOO_MUCH.format(50)),
             (state, FAAB, first, -1, TOO_MUCH.format(50)),
             (replace(state, claims=(replace(state.claims[0], bid=0),)), WEDNESDAY, first, 5,
              "This league has no FAAB bidding: leave the bid out.")]
    for before, settings, claim_id, bid, message in cases:
        with pytest.raises(ValueError) as refused:
            waivers.set_claim_bid(before, settings, claim_id, bid)
        assert str(refused.value) == message


# ---------------- the waiver run (F9-F12) ----------------

def test_the_highest_bid_wins_over_a_better_priority_and_pays():
    state = league()
    prize = wire(state)[0]
    claims = {FIRST: [claim(1, FIRST, prize, cuts(state, FIRST)[0], 5)],
              THIRD: [claim(2, THIRD, prize, cuts(state, THIRD)[0], 20)]}

    after = run(state, claims)

    assert owner(after, prize) == THIRD and after.faab_spent == {THIRD: 20}
    assert after.waiver_order[-1] == THIRD and after.waiver_order[0] == FIRST


def test_tied_bids_go_by_priority_which_rotates_after_each_award():
    state = league()
    one, two = wire(state)[:2]
    claims = {FIRST: [claim(1, FIRST, one, cuts(state, FIRST)[0], 10), claim(2, FIRST, two, cuts(state, FIRST)[1], 10)],
              SECOND: [claim(3, SECOND, one, cuts(state, SECOND)[0], 10),
                       claim(4, SECOND, two, cuts(state, SECOND)[1], 10)]}

    after = run(state, claims)

    assert (owner(after, one), owner(after, two)) == (FIRST, SECOND)       # FIRST went to the back after `one`
    assert after.faab_spent == {FIRST: 10, SECOND: 10}


def test_a_teams_own_order_breaks_a_tie_between_its_claims():
    state = league()
    one, two = wire(state)[:2]
    cut = cuts(state, FIRST)[0]
    claims = {FIRST: [claim(1, FIRST, two, cut, 10), claim(2, FIRST, one, cut, 10)]}

    after = run(state, claims)

    assert owner(after, two) == FIRST and owner(after, one) is None       # the second needed the same drop


def test_a_claim_that_fails_at_its_turn_is_skipped_for_good_and_logged_for_the_user():
    state = league()
    prize, other = wire(state)[:2]
    state = waivers.submit_claim(state, FAAB, know(), prize, cuts(state, USER)[0], 30)
    claims = {FIRST: [claim(1, FIRST, prize, cuts(state, FIRST)[0], 40)],
              SECOND: [claim(2, SECOND, prize, cuts(state, SECOND)[0], 35),
                       claim(3, SECOND, other, cuts(state, SECOND)[1], 1)]}

    after = run(state, claims)

    failed = [e.text for e in after.events if "failed" in e.text]
    assert owner(after, prize) == FIRST and owner(after, other) == SECOND
    assert after.faab_spent == {FIRST: 40, SECOND: 1}
    assert len(failed) == 1 and "is already on a roster (CPU 12)" in failed[0]


def test_a_claim_the_team_can_no_longer_pay_for_fails_and_no_budget_goes_negative():
    state = league(t1=70)
    first, second = wire(state)[:2]
    state = waivers.submit_claim(state, FAAB, know(), first, cuts(state, USER)[0], 20)
    state = waivers.submit_claim(state, FAAB, know(), second, cuts(state, USER)[1], 20)

    after = run(state, {})

    assert owner(after, first) == USER and owner(after, second) is None
    assert after.faab_spent == {USER: 90} and rs.faab_left(after, USER) == 10
    assert after.events[-1].text.endswith("failed. Not enough FAAB budget left ($10).")


def test_a_cpu_bid_above_its_budget_is_refused_too():
    state = league(t12=95)
    prize = wire(state)[0]

    after = run(state, {FIRST: [claim(1, FIRST, prize, cuts(state, FIRST)[0], 6)]})

    assert owner(after, prize) is None and after.faab_spent == {FIRST: 95}


def test_the_winning_bid_is_in_the_claim_text_only_in_faab_leagues():
    names = know().by_id
    for settings, price in ((FAAB, " for $7"), (WEDNESDAY, "")):
        state = league(settings)
        prize, cut = wire(state)[0], cuts(state, USER)[0]
        state = waivers.submit_claim(state, settings, know(), prize, cut, 7 if settings is FAAB else None)

        after = run(state, {}, settings)

        assert after.events[-1].text == f"You claimed {names[prize].name}{price}, dropping {names[cut].name}"


def test_wednesday_waivers_ignore_bids_and_spend_nothing():
    state = league(WEDNESDAY)
    prize = wire(state)[0]
    claims = {FIRST: [claim(1, FIRST, prize, cuts(state, FIRST)[0], 0)],
              THIRD: [claim(2, THIRD, prize, cuts(state, THIRD)[0], 50)]}

    after = run(state, claims, WEDNESDAY)

    assert owner(after, prize) == FIRST and after.faab_spent == {}


def test_free_agents_cost_nothing():
    state = league(phase="open")
    add = wire(state)[0]

    after = waivers.add_drop(state, FAAB, know(), USER, add, cuts(state, USER)[0])

    assert owner(after, add) == USER and after.faab_spent == {} and "$" not in after.events[-1].text


# ---------------- week flow and no hindsight (F13, F14) ----------------

def test_a_faab_league_plays_then_bids_then_opens_the_week():
    state = rg.auto_draft(rg.create_game(FAAB, DATA, 1), FAAB, DATA, 1)

    played = rg.advance(state, FAAB, DATA, 1, "play")
    add = next(v.player_id for v in rg.knowledge(played, FAAB, DATA).values if v.player_id not in rs.rostered(played))
    cut = rs.user_team(played).roster[-1]
    claimed = rg.set_claim_bid(rg.submit_claim(played, FAAB, DATA, add, cut, 3), FAAB, played.next_id, 4)
    after = rg.advance(claimed, FAAB, DATA, 1, "wednesday")

    assert (played.phase, played.week) == ("waivers", 2) and claimed.claims[0].bid == 4
    assert (after.phase, after.week, after.claims) == ("open", 2, ())
    assert all(0 <= rs.faab_left(after, t.id) <= rs.FAAB_BUDGET for t in after.teams)
    assert "set_claim_bid" in rg.__all__


def test_the_faab_run_does_not_depend_on_the_future():
    week = 6
    state = league(week=week, t12=60)
    prize, other = wire(state)[:2]
    state = waivers.submit_claim(state, FAAB, know(week), prize, cuts(state, USER)[0], 30)
    claims = {FIRST: [claim(1, FIRST, prize, cuts(state, FIRST)[0], 30)],
              SECOND: [claim(2, SECOND, other, cuts(state, SECOND)[0], 9)]}
    future = fakes.future_changed(DATA, after_week=week)

    real = waivers.process_claims(state, FAAB, know(week), claims)
    blind = waivers.process_claims(state, FAAB, fakes.fake_knowledge(week, FAAB, future), claims)
    advanced = [rg.advance(state, FAAB, data, 3, "wednesday") for data in (DATA, future)]

    assert rs.to_json(real) == rs.to_json(blind)
    assert rs.to_json(advanced[0]) == rs.to_json(advanced[1])


def test_a_cpus_later_claim_never_outbids_its_first_and_both_fit_the_budget(monkeypatch):
    from services import replay_claim_ai, replay_waiver_ai
    state = league(t2=70)                                                # CPU 2 has $30 left
    asked = iter([25, 28])                                               # the second, planned after the first, bids more
    monkeypatch.setattr(replay_claim_ai, "claim_bid", lambda *args: next(asked))

    first, second = replay_waiver_ai._priced(state, FAAB, 0, 2, [(1, None, 5.0), (2, None, 4.0)], 1.0, 100)

    assert (first.bid, second.bid) == (25, 5)                            # under the first and within $30 together
