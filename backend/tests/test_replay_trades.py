"""Replay League trades: the rules, a CPU's acceptance test, proposals, answers, and the scams it must stop.

All on fakes.drafted_state() (12 teams, the user is team 1). Three CPUs carry the stories:
- CPU 9 starts RB36 and RB32 but owns three good tight ends: it needs a back.
- CPU 5 owns seven receivers and its only tight end is its worst player: deep at WR.
- CPU 5 also owns fakes.INJURED_STAR_ID, who is out in weeks 6-9.
"""
import time
from dataclasses import replace

import pytest

from services import replay_state as rs, replay_trades as rt, trade
from services.replay_pool import SKILL
from tests import replay_fakes as fakes

P = fakes.player_id
SETTINGS = fakes.fake_settings()
USER, RICH, DEEP_AT_WR, NEEDS_RB = 1, 2, 5, 9
RB14, RB23, TE7, TE6 = P("RB", 14), P("RB", 23), P("TE", 7), P("TE", 6)      # RB14, RB23: the user's bench backs
SCRUB, SCRUB2, SCRUB3 = P("WR", 54), P("WR", 51), P("WR", 42)                # the user's last receivers
FAIR = (NEEDS_RB, (RB14,), (TE7,))          # the user's third back for CPU 9's second tight end: helps both
STAR_SALE = ((fakes.INJURED_STAR_ID,), (P("QB", 2),))     # week 6: CPU 5's injured back for CPU 2's quarterback
EVALUATE_BUDGET_SECONDS = 0.25


def league(week: int = 1, settings: rs.Settings = SETTINGS):
    return replace(fakes.drafted_state(settings), week=week), fakes.fake_knowledge(week, settings)


def judge(to_team, give, get, drops=(), week=1, settings=SETTINGS, from_team=USER, state=None) -> rt.Verdict:
    drafted, know = league(week, settings)
    return rt.evaluate(state or drafted, settings, know, from_team, to_team, give, get, drops)


def as_user(state: rs.State, team_id: int) -> rs.State:
    """The same league with another team as the user's."""
    return replace(state, teams=tuple(replace(t, is_user=t.id == team_id) for t in state.teams))


def said(verdict: rt.Verdict, words: str) -> bool:
    return any(words in reason for reason in verdict.reasons)


# ---------------- value ----------------

def test_a_lopsided_one_for_one_is_declined_on_value():
    verdict = judge(RICH, [SCRUB], [P("RB", 2)])

    assert not verdict.accepted and said(verdict, "Value is too far apart")
    assert verdict.value_give < verdict.value_get and verdict.percent > 0.9 and verdict.fair_label == "big_win"
    assert verdict.give_values == ((SCRUB, verdict.value_give),) and verdict.get_values[0][0] == P("RB", 2)
    assert verdict.their_gain < 0 < verdict.your_gain


@pytest.mark.parametrize("to_team, give, star", [(4, (RB14, RB23), P("WR", 8)), (4, (SCRUB3, SCRUB2), P("WR", 1))])
def test_two_bench_players_for_a_star_is_declined(to_team, give, star):
    verdict = judge(to_team, give, [star])

    assert not verdict.accepted and said(verdict, "Value is too far apart") and said(verdict, "lineup worse")
    assert verdict.value_give < verdict.value_get
    assert len(verdict.their_drops) == 1                     # the CPU would have to cut someone to take two
    if give == (RB14, RB23):                                 # worth more than the star added up, less as a package
        raw = [value for _, value in verdict.give_values]
        assert sum(raw) > verdict.value_get > verdict.value_give == trade.adjusted_total(raw)


def test_hard_difficulty_needs_a_higher_multiple_than_normal():
    hard = fakes.fake_settings(difficulty="hard")

    normal_verdict = judge(NEEDS_RB, [RB14], [TE6])
    hard_verdict = judge(NEEDS_RB, [RB14], [TE6], settings=hard)

    assert 1.0 < normal_verdict.value_give / normal_verdict.value_get < rs.USER_TRADE_MULTIPLE["hard"]
    assert normal_verdict.accepted and not hard_verdict.accepted
    assert hard_verdict.reasons[0].endswith("They want 8% more than they give.")


def test_a_value_decline_suggests_the_cheapest_player_that_closes_the_gap():
    hard = fakes.fake_settings(difficulty="hard")

    short = judge(NEEDS_RB, [RB14], [TE6], settings=hard)
    fixed = judge(NEEDS_RB, [RB14, SCRUB], [TE6], settings=hard)
    full = judge(RICH, [SCRUB, SCRUB2, SCRUB3], [P("RB", 2)])

    assert short.suggestion == "Add Replay WR54 to make this fair."
    assert not said(fixed, "Value is too far apart") and fixed.suggestion is None
    assert full.suggestion.startswith("Add about ") and full.suggestion.endswith(" points of value to make this fair.")
    assert judge(DEEP_AT_WR, [P("WR", 31)], [P("WR", 24)]).suggestion is None      # value was not the problem


# ---------------- lineup ----------------

def test_a_fair_deal_that_fills_a_cpu_need_is_accepted_and_carried_out():
    state, know = league()

    after, verdict = rt.propose(state, SETTINGS, know, *FAIR)

    assert verdict.accepted and verdict.reasons == () and verdict.their_gain >= rt.MIN_TRADE_GAIN
    assert RB14 in rs.team(after, NEEDS_RB).roster and TE7 not in rs.team(after, NEEDS_RB).roster
    assert TE7 in rs.user_team(after).roster and RB14 not in rs.user_team(after).roster
    assert all(len(t.roster) == SETTINGS.roster_size for t in after.teams)
    assert after.traded == {RB14: 1, TE7: 1}
    assert after.offers == (rs.Offer(id=state.next_id, week=1, from_team=USER, to_team=NEEDS_RB, give=(RB14,),
                                     get=(TE7,), status="accepted"),)
    event = after.events[-1]
    assert (event.kind, event.team_id, event.player_ids) == ("trade", USER, (RB14, TE7))
    assert event.text == "You traded Replay RB14 to CPU 9 for Replay TE7"
    assert rs.has_traded_this_week(after, NEEDS_RB) and after.locked == ()
    assert RB14 in rs.user_team(state).roster and state.offers == ()         # the input is untouched
    assert rs.state_from_json(rs.to_json(after)) == after


def test_a_cpu_deep_at_receiver_declines_a_fair_receiver_swap_but_takes_the_back_it_needs():
    swap = judge(DEEP_AT_WR, [P("WR", 31)], [P("WR", 24)])
    fixes_a_hole = judge(DEEP_AT_WR, [RB14], [P("WR", 22)])

    assert swap.value_give >= swap.value_get and swap.fair_label == "fair"
    assert swap.reasons == ("It doesn't improve CPU 5's lineup: they are already deep at WR.",)
    assert 0 <= swap.their_gain < rt.MIN_TRADE_GAIN
    assert fixes_a_hole.accepted and fixes_a_hole.their_gain > rt.MIN_TRADE_GAIN


def test_asking_again_gives_the_same_verdict_and_a_declined_proposal_is_kept_once_and_moves_nobody():
    state, know = league()

    declined, verdict = rt.propose(state, SETTINGS, know, RICH, [SCRUB], [P("RB", 2)])
    rt.clear_cache()
    again, same_verdict = rt.propose(declined, SETTINGS, know, RICH, [SCRUB], [P("RB", 2)])

    assert not verdict.accepted and verdict == same_verdict and judge(*FAIR) == judge(*FAIR)
    assert [(o.status, o.from_team, o.to_team, o.give) for o in declined.offers] == [("declined", USER, RICH, (SCRUB,))]
    assert declined.teams == state.teams and declined.events == () and declined.traded == {}
    assert again is declined


# ---------------- kickers, defenses and legal lineups ----------------

def test_a_kicker_and_a_defense_cannot_balance_a_lopsided_deal():
    plain = judge(RICH, [SCRUB3], [P("WR", 11)])
    padded = judge(RICH, [SCRUB3, P("K", 5), P("DST", 8)], [P("WR", 11)])

    kicker, defense = dict(padded.give_values)[P("K", 5)], dict(padded.give_values)[P("DST", 8)]
    assert not plain.accepted and not padded.accepted and said(padded, "Value is too far apart")
    assert 0 < kicker < plain.value_get / 5 and 0 < defense < plain.value_get / 5
    assert padded.value_give < plain.value_get / 2


@pytest.mark.parametrize("position, only_one", [("TE", P("TE", 18)), ("K", P("K", 5)), ("DST", P("DST", 8))])
def test_the_user_must_keep_a_full_lineup_too(position, only_one):
    verdict = judge(DEEP_AT_WR, [only_one], [P("WR", 48)])

    assert not verdict.accepted
    assert said(verdict, f"It would leave You without a full starting lineup (short at {position}).")


# ---------------- league rules ----------------

def test_a_traded_player_cannot_be_traded_again_for_two_weeks():
    state, know = league()
    traded, _ = rt.propose(state, SETTINGS, know, *FAIR)
    flip = (DEEP_AT_WR, [TE7], [P("WR", 44)])

    next_week = judge(*flip, week=2, state=replace(traded, week=2))
    week_after = judge(*flip, week=3, state=replace(traded, week=3))

    assert next_week.reasons == ("Traded in the last 2 weeks, so not yet tradable again: Replay TE7.",)
    assert week_after.accepted


def test_a_cpu_trades_once_a_week_but_the_user_may_deal_with_another_cpu():
    state, know = league()
    traded, _ = rt.propose(state, SETTINGS, know, *FAIR)

    same_cpu = rt.evaluate(traded, SETTINGS, know, USER, NEEDS_RB, [RB23], [P("WR", 61)])
    other_cpu = rt.evaluate(traded, SETTINGS, know, USER, DEEP_AT_WR, [RB23], [P("WR", 44)])

    assert same_cpu.reasons == ("CPU 9 has already made a trade this week.",)
    assert other_cpu.accepted


def test_no_trades_after_the_deadline_or_outside_the_season():
    assert isinstance(judge(*FAIR, week=SETTINGS.trade_deadline_week), rt.Verdict)      # the last open week
    with pytest.raises(ValueError, match="The trade deadline has passed: no trades after week 11."):
        judge(*FAIR, week=SETTINGS.trade_deadline_week + 1)
    for phase in ("draft", "complete"):
        with pytest.raises(ValueError, match="Trades are open"):
            judge(*FAIR, state=replace(fakes.drafted_state(), phase=phase))


@pytest.mark.parametrize("to_team, give, get, message", [
    (NEEDS_RB, (RB14, SCRUB, SCRUB2, SCRUB3), (TE7,), "at most 3 players on each side"),
    (NEEDS_RB, (), (TE7,), "Both sides of a trade need at least one player."),
    (NEEDS_RB, (RB14, RB14), (TE7,), "listed more than once"),
    (NEEDS_RB, (TE7,), (RB14,), "Not on You's roster: Replay TE7."),
    (NEEDS_RB, (RB14,), (P("WR", 22),), "Not on CPU 9's roster: Replay WR22."),
    (USER, (RB14,), (RB23,), "can't trade with itself"),
    (99, (RB14,), (TE7,), "No team 99 in this league."),
])
def test_impossible_input_is_a_clear_error(to_team, give, get, message):
    with pytest.raises(ValueError, match=message):
        judge(to_team, give, get)


def test_the_user_is_always_the_proposer_and_values_must_be_this_weeks():
    state, know = league()

    with pytest.raises(ValueError, match="from your side"):
        rt.evaluate(state, SETTINGS, know, NEEDS_RB, USER, [TE7], [RB14])
    with pytest.raises(ValueError, match="values are for week 1, but the league is in week 2"):
        rt.evaluate(replace(state, week=2), SETTINGS, know, USER, NEEDS_RB, [RB14], [TE7])


# ---------------- roster limits and drops ----------------

def test_a_roster_overflow_needs_the_users_own_drops():
    two_for_one = (NEEDS_RB, [RB14], [TE7, P("WR", 61)])

    with pytest.raises(ValueError, match="leaves you 1 over the roster limit of 15: choose exactly 1 to drop"):
        judge(*two_for_one)
    with pytest.raises(ValueError, match="on your roster and not part of the trade"):
        judge(*two_for_one, drops=[RB14])
    with pytest.raises(ValueError, match="on your roster and not part of the trade"):
        judge(*two_for_one, drops=[P("WR", 22)])
    with pytest.raises(ValueError, match="doesn't need you to drop anyone"):
        judge(*FAIR, drops=[SCRUB])
    assert judge(*two_for_one, drops=[SCRUB]).your_drops == (SCRUB,)


def test_the_users_drop_goes_on_waivers_when_the_trade_goes_through():
    state, know = league()

    after, verdict = rt.propose(state, SETTINGS, know, NEEDS_RB, [RB14], [TE7, P("WR", 61)], [SCRUB])

    assert verdict.accepted and after.locked == (SCRUB,)
    assert SCRUB not in rs.rostered(after) and len(rs.user_team(after).roster) == SETTINGS.roster_size
    assert [(e.kind, e.team_id, e.player_ids) for e in after.events[-2:]] == [
        ("trade", USER, (RB14, TE7, P("WR", 61))), ("drop", USER, (SCRUB,))]
    assert len(rs.team(after, NEEDS_RB).roster) == SETTINGS.roster_size - 1       # their spot stays open


def test_a_cpu_forced_to_drop_cuts_its_least_valuable_skill_player_that_keeps_the_lineup_legal():
    state, know = league()
    before = [know.by_id[pid] for pid in rs.team(state, DEEP_AT_WR).roster]
    worst = min((v for v in before if v.position in SKILL), key=lambda v: (v.healthy_value, v.ros_ppg))
    assert worst.player_id == P("TE", 25) and sum(v.position == "TE" for v in before) == 1

    after, verdict = rt.propose(state, SETTINGS, know, DEEP_AT_WR, [RB14, SCRUB], [P("WR", 22)])

    kept = [know.by_id[pid].position for pid in rs.team(after, DEEP_AT_WR).roster]
    assert verdict.accepted and verdict.their_drops == (SCRUB,)       # not its only tight end, kicker or defense
    assert len(kept) == SETTINGS.roster_size and {"TE", "K", "DST"} <= set(kept)
    assert after.locked == (SCRUB,) and after.events[-1].kind == "drop"


# ---------------- answering a CPU's offer ----------------

def offered(give=(TE7,), get=(RB14,), from_team=NEEDS_RB, week=1):
    """A league where a CPU has a pending offer out to the user: it sends `give` for the user's `get`."""
    state, know = league(week)
    return rt.record_offer(state, from_team, USER, give, get, "pending"), know


def test_accepting_an_offer_carries_it_out_under_the_same_offer():
    state, know = offered()
    offer = rt.offers_for_user(state)[0]

    after = rt.respond(state, SETTINGS, know, offer.id, accept=True)

    assert after.offers == (replace(offer, status="accepted"),) and rt.offers_for_user(after) == ()
    assert TE7 in rs.user_team(after).roster and RB14 in rs.team(after, NEEDS_RB).roster
    assert after.traded == {RB14: 1, TE7: 1} and after.events[-1].kind == "trade"
    with pytest.raises(ValueError, match="That offer is already accepted."):
        rt.respond(after, SETTINGS, know, offer.id, accept=True)


def test_declining_an_offer_only_marks_it_declined():
    state, know = offered()

    after = rt.respond(state, SETTINGS, know, state.offers[0].id, accept=False)

    assert after.offers[0].status == "declined" and after.teams == state.teams and after.events == ()
    with pytest.raises(ValueError, match="no such offer"):
        rt.respond(state, SETTINGS, know, 999, accept=True)


@pytest.mark.parametrize("change, why", [
    (lambda s: s, "Value is too far apart"),                                        # the CPU's test fails now
    (lambda s: rs.release(s, SETTINGS, RICH, P("RB", 2)), "Not on CPU 2's roster"),   # the player is gone
    (lambda s: replace(s, week=2), "It was made in week 1."),                       # last week's offer
])
def test_an_offer_that_no_longer_stands_expires_with_the_reason(change, why):
    made, _ = offered(give=(P("RB", 2),), get=(SCRUB,), from_team=RICH)
    state = change(made)
    know = fakes.fake_knowledge(state.week, SETTINGS)

    with pytest.raises(rt.OfferExpired, match=why) as error:
        rt.respond(state, SETTINGS, know, made.offers[0].id, accept=True)

    assert str(error.value).startswith("That offer no longer stands. ")
    assert error.value.state.offers[0].status == "expired" and error.value.state.teams == state.teams
    assert state.offers[0].status == "pending"


def test_missing_drops_leave_an_offer_open():
    state, know = offered(give=(TE7, P("WR", 61)))
    offer_id = state.offers[0].id

    with pytest.raises(ValueError, match="choose exactly 1 to drop") as error:
        rt.respond(state, SETTINGS, know, offer_id, accept=True)
    after = rt.respond(state, SETTINGS, know, offer_id, accept=True, user_drops=[SCRUB])

    assert not isinstance(error.value, rt.OfferExpired)
    assert after.offers[0].status == "accepted" and after.locked == (SCRUB,)


def test_only_pending_offers_to_the_user_are_listed():
    state, _ = offered()
    state = rt.record_offer(state, USER, RICH, (SCRUB,), (P("RB", 2),), "declined")
    state = rt.record_offer(state, RICH, DEEP_AT_WR, (P("QB", 2),), (P("WR", 22),), "accepted")

    assert [o.from_team for o in rt.offers_for_user(state)] == [NEEDS_RB]


# ---------------- speed and hindsight ----------------

def test_evaluate_is_fast():
    state, know = league()
    rt.evaluate(state, SETTINGS, know, USER, RICH, [SCRUB], [P("RB", 2)])       # warms the shared projector
    rt.clear_cache()

    started = time.perf_counter()
    rt.evaluate(state, SETTINGS, know, USER, *FAIR)

    assert time.perf_counter() - started < EVALUATE_BUDGET_SECONDS


@pytest.mark.parametrize("week", [1, 6, 9])
def test_verdicts_do_not_change_when_the_future_does(week):
    state = replace(fakes.drafted_state(), week=week)
    changed = fakes.fake_knowledge(week, data=fakes.future_changed(fakes.fake_season(), after_week=week))
    deals = [(USER, *FAIR), (USER, RICH, (SCRUB,), (P("RB", 2),)), (USER, DEEP_AT_WR, (RB14, SCRUB), (P("WR", 22),)),
             (DEEP_AT_WR, RICH, *STAR_SALE)]

    real = fakes.fake_knowledge(week)
    assert real is not changed
    assert all(rt.evaluate(state, SETTINGS, changed, *d) == rt.evaluate(state, SETTINGS, real, *d) for d in deals)


# ---------------- scams from the design review ----------------

class TestExploits:
    def test_the_user_cannot_dump_a_player_who_just_got_hurt_though_cpus_may_trade_him(self):
        state, know = league(week=6)
        seller = as_user(state, DEEP_AT_WR)                 # the user owns the injured star, knows he is out a month

        between_cpus = rt.evaluate(state, SETTINGS, know, DEEP_AT_WR, RICH, *STAR_SALE)
        with_user = rt.evaluate(seller, SETTINGS, know, DEEP_AT_WR, RICH, *STAR_SALE)

        assert know.by_id[fakes.INJURED_STAR_ID].out and between_cpus.accepted and not with_user.accepted
        assert said(with_user, "They won't deal for or away a player who is out right now: Injured Star.")

    def test_the_user_cannot_buy_an_injured_star_cheaply_before_he_returns(self):
        state, know = league(week=fakes.INJURED_WEEKS[-1])
        buyer = as_user(state, RICH)                        # the user knows he is back next week

        verdict = rt.evaluate(buyer, SETTINGS, know, RICH, DEEP_AT_WR, [P("QB", 2)], [fakes.INJURED_STAR_ID])

        assert said(verdict, "They won't deal for or away a player who is out right now: Injured Star.")
        assert not verdict.accepted

    def test_a_cpu_cannot_be_stripped_of_its_only_kicker_and_defense(self):
        with_throw_ins = judge(NEEDS_RB, [RB14], [TE7, P("K", 10), P("DST", 4)], [SCRUB, SCRUB2])

        assert judge(*FAIR).accepted and not with_throw_ins.accepted
        assert with_throw_ins.value_get > judge(*FAIR).value_get
        assert said(with_throw_ins, "It would leave CPU 9 without a full starting lineup (short at K, DST).")
        assert judge(NEEDS_RB, [RB14, P("K", 5)], [TE7, P("K", 10)]).accepted       # a kicker for a kicker is fine

    def test_a_worthless_bench_player_still_costs_value_as_a_throw_in(self):
        tight = judge(NEEDS_RB, [RB23], [TE7])
        with_extra = judge(NEEDS_RB, [RB23], [TE7, P("WR", 61)], [SCRUB])

        floor = trade.FAIR_POINTS_PER_WEEK * fakes.fake_knowledge(1).by_id[P("WR", 61)].games_left
        assert tight.accepted and dict(with_extra.get_values)[P("WR", 61)] >= floor > 0
        assert not with_extra.accepted and said(with_extra, "Value is too far apart")

    def test_worthless_bench_players_cannot_buy_a_star(self):
        verdict = judge(RICH, [SCRUB, SCRUB2, SCRUB3], [P("WR", 11)])

        assert not verdict.accepted and verdict.value_give < verdict.value_get / 2
        assert len(verdict.their_drops) == 2

    def test_a_player_a_cpu_drops_to_make_room_goes_on_waivers_not_to_the_user(self):
        state, know = league()

        after, verdict = rt.propose(state, SETTINGS, know, NEEDS_RB, [RB14, SCRUB], [TE7])

        assert verdict.accepted and verdict.their_drops
        assert set(verdict.their_drops) <= set(after.locked) and not set(verdict.their_drops) & rs.rostered(after)
