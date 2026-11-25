"""Replay League: CPU judgment fixes from the 2026 Sim league (league 7), on the fake league.

- Waivers keep recent high draft picks, injured players with trade value and, at an over-full position, the
  player the market values more (services/replay_waiver_ai.py).
- A CPU's own offer to the user gives him its persona's edge, and still stands when he accepts it.
- A trade or a drop expires every other pending offer naming a player who moved.
- A player picked up this week and worth under 100 counts half in trades until the next week.
- Lineup solves are cached per projector, with the same answers.
"""
from dataclasses import replace
from types import MappingProxyType

import pytest

from services import (lineup_gain, replay_personas as rp, replay_state as rs, replay_trade_value as tv,
                      replay_trades as rt, replay_values as rv, replay_waiver_ai as ai, replay_waivers)
from services.replay_news import Report
from services.replay_pool import SKILL
from tests import replay_fakes as fakes
from tests.test_replay_trades import NEEDS_RB, RB14, RICH, SCRUB, SETTINGS, TE7, USER, league as chart_league

P = fakes.player_id


def know(week: int):
    return fakes.fake_knowledge(week, SETTINGS)


def drafted(week: int, phase: str = "waivers") -> rs.State:
    return replace(fakes.drafted_state(SETTINGS), week=week, phase=phase)


def with_values(knowledge, *changed):
    """`knowledge` with these ReplayValues in place of the ones with their ids."""
    by_id = {**knowledge.by_id, **{v.player_id: v for v in changed}}
    return replace(knowledge, values=tuple(by_id[v.player_id] for v in knowledge.values),
                   by_id=MappingProxyType(by_id))


# ---------------- waivers: who a CPU keeps ----------------

def test_picks_from_the_first_eight_rounds_are_protected_through_week_four():
    early = {pick.player_id for pick in drafted(1).picks if pick.round <= ai.EARLY_ROUNDS}

    for week in (1, 2, 3, 4):
        assert ai.early_picks(drafted(week), SETTINGS, know(week)) == early
        assert early <= ai._context(know(week), SETTINGS, drafted(week)).protected
    assert ai.early_picks(drafted(5), SETTINGS, know(5)) == frozenset()


def test_a_jump_in_league_counts_its_four_weeks_from_its_first_week():
    late = fakes.fake_settings(start_week=6)
    state = replace(fakes.drafted_state(SETTINGS), week=9)

    assert ai.early_picks(state, late, know(9)) and not ai.early_picks(replace(state, week=10), late, know(10))


def test_an_early_pick_out_for_the_season_is_not_protected():
    knowledge = know(2)
    pick = next(p for p in drafted(2).picks if p.round == ai.EARLY_ROUNDS)
    gone = replace(knowledge.by_id[pick.player_id], out=True,
                   news=Report(since=1, back=None, text="Out for the season (IR since week 1)."))
    hurt = replace(gone, news=Report(since=1, back=6, text="Out (IR) since week 1; expected back week 6."))

    assert pick.player_id not in ai.early_picks(drafted(2), SETTINGS, with_values(knowledge, gone))
    assert pick.player_id in ai.early_picks(drafted(2), SETTINGS, with_values(knowledge, hurt))


def test_no_cpu_claim_in_the_first_weeks_drops_a_round_one_to_eight_pick():
    for week in (2, 3, 4):
        state, knowledge = drafted(week), know(week)
        early = ai.early_picks(state, SETTINGS, knowledge)
        claims = ai.cpu_claims(state, SETTINGS, knowledge, 7)
        assert not {c.drop for cs in claims.values() for c in cs} & early


def test_an_injured_player_with_trade_value_and_a_return_date_is_kept():
    knowledge = know(8)
    base = next(v for v in knowledge.values if v.position == "WR" and v.healthy_chart > 0)
    # 2026: Jordyn Tyson, tagged IR, healthy chart 63, news "expected back week 9", cut in week 8.
    tyson = replace(base, out=True, projection=0.0, last_projection=0.0, healthy_chart=63.0, healthy_value=0.0,
                    news=Report(since=1, back=9, text="Out (IR) since week 1; expected back week 9."))
    cheap = replace(tyson, healthy_chart=ai.KEEP_OUT_CHART - 1)
    gone = replace(tyson, news=Report(since=1, back=None, text="Out for the season (IR since week 1)."))
    ghost = replace(tyson, news=None)                       # out without a report: never had a projection

    assert ai.droppable([tyson], knowledge, frozenset()) == []
    for value in (cheap, gone, ghost):
        assert ai.droppable([value], knowledge, frozenset()) == [value]


def test_close_model_values_are_settled_by_trade_value():
    knowledge = know(10)
    base = next(v for v in knowledge.values if v.position == "TE")
    loveland = replace(base, player_id=1, healthy_value=0.0, healthy_chart=169.0)
    ertz = replace(base, player_id=2, healthy_value=2.0, healthy_chart=40.0)
    useful = replace(base, player_id=3, healthy_value=30.0, healthy_chart=10.0)

    assert ai._close_by_chart([loveland, ertz, useful]) == [ertz, loveland, useful]


def test_the_trim_cuts_the_cheaper_of_two_players_the_model_cannot_tell_apart():
    state, knowledge = drafted(3, phase="open"), know(3)
    team = rs.team(state, 9)                                # three tight ends: one over the limit
    tight_ends = [knowledge.by_id[pid] for pid in team.roster if knowledge.by_id[pid].position == "TE"]
    assert len(tight_ends) == rv.position_cap("TE", SETTINGS) + 1
    star, pricey, cheap = (replace(tight_ends[0], healthy_value=500.0), replace(tight_ends[1], healthy_value=0.0,
                           healthy_chart=169.0), replace(tight_ends[2], healthy_value=0.0, healthy_chart=14.0))
    knowledge = with_values(knowledge, star, pricey, cheap)
    ctx = replace(ai._context(knowledge, SETTINGS), protected=frozenset())
    roster = [knowledge.by_id[pid] for pid in team.roster]

    move = ai._trim(roster, ai._unrostered(state, knowledge), ctx)

    assert move is not None and move[1] == cheap.player_id


# ---------------- trades: position limits ----------------

def test_a_trade_that_takes_a_cpu_past_a_position_limit_plans_the_cut_there():
    # 2026: Two-Minute Drill took Colston Loveland as a third tight end, then cut him the next week.
    state, knowledge = chart_league()
    two_qbs, spare_qb = 8, P("QB", 18)
    theirs = [knowledge.by_id[pid] for pid in rs.team(state, two_qbs).roster]
    quarterbacks = [v for v in theirs if v.position == "QB"]
    assert len(quarterbacks) == rv.position_cap("QB", SETTINGS)

    for back in (v.player_id for v in theirs if v.position in ("RB", "WR")):
        verdict = rt.evaluate(state, SETTINGS, knowledge, USER, two_qbs, [spare_qb], [back])
        cheapest = min([*quarterbacks, knowledge.by_id[spare_qb]],
                       key=lambda v: (v.healthy_chart, v.healthy_value, v.ros_ppg, v.player_id))
        assert verdict.their_drops == (cheapest.player_id,)
        if cheapest.player_id == spare_qb:                 # he would be cut: he counts for nothing
            assert not verdict.accepted


def test_a_carried_out_trade_leaves_the_cpu_within_the_limit():
    state, knowledge = chart_league()

    def count(team, position):
        return sum(knowledge.by_id[pid].position == position for pid in rs.held(team))

    user = rs.user_team(state)
    found = next((cpu.id, (mine,), (theirs,), knowledge.by_id[mine].position)
                 for cpu in state.teams[1:] for mine in user.roster for theirs in cpu.roster
                 if knowledge.by_id[mine].position in SKILL and knowledge.by_id[theirs].position in SKILL
                 and knowledge.by_id[mine].position != knowledge.by_id[theirs].position
                 and count(cpu, knowledge.by_id[mine].position) == rv.position_cap(knowledge.by_id[mine].position,
                                                                                    SETTINGS)
                 and rt.evaluate(state, SETTINGS, knowledge, USER, cpu.id, (mine,), (theirs,)).accepted)
    cpu_id, give, get, position = found

    after, verdict = rt.propose(state, SETTINGS, knowledge, cpu_id, give, get)

    assert verdict.accepted and [knowledge.by_id[pid].position for pid in verdict.their_drops] == [position]
    assert count(rs.team(after, cpu_id), position) == rv.position_cap(position, SETTINGS)


# ---------------- a CPU's own offer to the user ----------------

def test_each_persona_pays_its_edge_careful_ones_least():
    assert rp.offer_edge("steady")[1] < rp.offer_edge("balanced")[1] < rp.offer_edge("panicky")[1] == 0.10
    assert all(0.05 <= low <= high <= 0.10 for low, high in rp.OFFER_EDGE.values())
    assert rt.offer_multiple(replace(rs.team(drafted(1), 2), persona="steady")) == pytest.approx(
        1 / (1.06 * tv.OUT_MARGIN))


def test_an_offer_the_cpu_made_still_stands_when_the_user_accepts_it():
    """respond re-checks a CPU's offer as its own (offer_multiple), so a deal it would not take as the user's
    proposal, but offered itself, goes through instead of expiring."""
    state, knowledge = chart_league()
    state = rs.with_team(state, replace(rs.team(state, NEEDS_RB), persona="panicky"))
    user, cpu = rs.user_team(state), rs.team(state, NEEDS_RB)

    def judged(mine, theirs, offered):
        return rt.evaluate(state, SETTINGS, knowledge, USER, NEEDS_RB, (mine,), (theirs,), offered=offered)

    mine, theirs = next((m, t) for m in user.roster for t in cpu.roster
                        if knowledge.by_id[m].position in SKILL and knowledge.by_id[t].position in SKILL
                        and judged(m, t, True).accepted and not judged(m, t, False).accepted)
    made = rt.record_offer(state, NEEDS_RB, USER, (theirs,), (mine,), "pending")

    after = rt.respond(made, SETTINGS, knowledge, made.offers[-1].id, accept=True)

    assert after.offers[-1].status == "accepted" and theirs in rs.user_team(after).roster


# ---------------- stale offers ----------------

def test_a_done_deal_expires_every_other_pending_offer_with_a_player_who_moved():
    state, knowledge = chart_league()
    pending = rt.record_offer(state, NEEDS_RB, USER, (TE7,), (RB14,), "pending")
    other = rt.record_offer(pending, RICH, USER, (P("RB", 2),), (SCRUB,), "pending")
    verdict = rt.evaluate(other, SETTINGS, knowledge, USER, NEEDS_RB, (RB14,), (TE7,))
    assert verdict.accepted

    after, _ = rt.propose(other, SETTINGS, knowledge, NEEDS_RB, (RB14,), (TE7,))

    statuses = {o.id: o.status for o in after.offers}
    assert statuses[pending.offers[-1].id] == "expired"
    assert statuses[other.offers[-1].id] == "pending"             # nobody in it moved
    assert [o.id for o in rt.offers_for_user(after)] == [other.offers[-1].id]


def test_a_drop_expires_the_pending_offers_naming_him():
    # 2026: an offer for Tee Higgins and Tony Pollard stayed open after the user traded for them.
    state, _ = chart_league()
    made = rt.record_offer(state, NEEDS_RB, USER, (TE7,), (RB14,), "pending")

    after = rs.release(made, SETTINGS, USER, RB14)

    assert after.offers[-1].status == "expired" and rt.offers_for_user(after) == ()


# ---------------- just picked up ----------------

def picked_up(state: rs.State, team_id: int, player_id: int) -> rs.State:
    return rs.add_event(state, "add", team_id, "added", (player_id,))


def test_a_cheap_pickup_counts_half_in_his_first_week_and_in_full_the_next():
    state, knowledge = chart_league()
    user = rs.user_team(state)
    cheap = next(pid for pid in user.roster if knowledge.by_id[pid].position in SKILL
                 and 0 < knowledge.by_id[pid].chart < tv.PICKUP_CHART_MAX)
    fresh = picked_up(state, USER, cheap)
    deal = (USER, RICH, (cheap,), (P("RB", 40),))

    plain = rt.evaluate(state, SETTINGS, knowledge, *deal)
    first = rt.evaluate(fresh, SETTINGS, knowledge, *deal)
    next_week = rt.evaluate(replace(fresh, week=2), SETTINGS, chart_league(2)[1], *deal)

    assert rs.pickups(fresh) == {cheap} and rs.pickups(replace(fresh, week=2)) == frozenset()
    assert dict(first.give_values)[cheap] == pytest.approx(knowledge.by_id[cheap].chart * tv.PICKUP_SHARE)
    assert first.value_give < plain.value_give and first.score < plain.score      # the CPU counts him half too
    assert dict(next_week.give_values)[cheap] == chart_league(2)[1].by_id[cheap].chart


def test_a_pickup_worth_a_hundred_or_more_is_not_discounted():
    state, knowledge = chart_league()
    star = next(pid for pid in rs.user_team(state).roster if knowledge.by_id[pid].chart >= tv.PICKUP_CHART_MAX)
    deal = (USER, RICH, (star,), (P("RB", 40),))

    assert rt.evaluate(picked_up(state, USER, star), SETTINGS, knowledge, *deal) == rt.evaluate(
        state, SETTINGS, knowledge, *deal)


def test_a_real_waiver_add_marks_him_and_a_failed_claim_or_a_later_drop_does_not():
    state, knowledge = drafted(3, phase="open"), know(3)
    free = next(v.player_id for v in ai._unrostered(state, knowledge) if v.position == "WR")
    drop = next(pid for pid in rs.user_team(state).roster if knowledge.by_id[pid].position == "WR")

    added = replay_waivers.execute_move(state, SETTINGS, knowledge, USER, free, drop)
    failed = rs.add_event(state, "claim", USER, "Your claim failed.", (free,))
    let_go = rs.release(added, SETTINGS, USER, free)

    assert rs.pickups(added) == {free}
    assert rs.pickups(failed) == rs.pickups(let_go) == frozenset()
    assert rs.pickups(rs.state_from_json(rs.to_json(added))) == {free}        # saves (old ones too) need no field


def test_the_trade_screen_notes_the_pickup_discount():
    from services import replay_views_league as views

    state, knowledge = chart_league()
    cheap = next(pid for pid in rs.user_team(state).roster if knowledge.by_id[pid].position in SKILL
                 and 0 < knowledge.by_id[pid].chart < tv.PICKUP_CHART_MAX)
    found = rt.evaluate(picked_up(state, USER, cheap), SETTINGS, knowledge, USER, RICH, (cheap,), (P("RB", 40),))
    game = type("G", (), {"know": knowledge})()

    shown = views.verdict(game, found)

    assert shown["give"][0]["note"] == views.PICKUP_NOTE and shown["get"][0]["note"] is None


# ---------------- lineup cache ----------------

def test_lineup_solves_are_cached_with_the_same_answer_whatever_the_roster_order():
    knowledge = know(4)
    roster = [knowledge.by_id[pid] for pid in drafted(4).teams[3].roster]
    fresh = rv.projector.__wrapped__(knowledge)

    first = lineup_gain.week_lineup(roster, 5, fresh, knowledge.league)
    again = lineup_gain.week_lineup(list(reversed(roster)), 5, fresh, knowledge.league)
    other = lineup_gain.week_lineup(roster, 5, rv.projector.__wrapped__(knowledge), knowledge.league)

    assert again is first and other == first and first[0] > 0
