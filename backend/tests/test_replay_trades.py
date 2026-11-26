"""Replay League trades: the rules, a CPU's acceptance test, proposals, answers, counters and the scams it must stop.

All on fakes.drafted_state() (12 teams, the user is team 1). Values come from `charted`: a plain rank-based
chart built here from each week's healthy values, so these tests do not depend on replay_chart's formula.
- CPU 9 starts RB36 and RB32 but owns three good tight ends: it needs a back.
- CPU 5 owns seven receivers; its only tight end, TE25, is its worst player.
"""
import itertools
import math
import re
import time
from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType, SimpleNamespace

import pytest

from services import replay_personas as rp, replay_state as rs, replay_trade_value as tv, replay_trades as rt
from services.replay_personas import PERSONAS
from services.replay_pool import SKILL
from tests import replay_fakes as fakes

P = fakes.player_id
SETTINGS = fakes.fake_settings()
USER, RICH, DEEP_AT_WR, NEEDS_RB = 1, 2, 5, 9
RB14, RB23, TE7, TE6 = P("RB", 14), P("RB", 23), P("TE", 7), P("TE", 6)      # RB14, RB23: the user's bench backs
SCRUB, SCRUB2, SCRUB3 = P("WR", 54), P("WR", 51), P("WR", 42)                # the user's last receivers
FAIR = (NEEDS_RB, (RB14,), (TE7,))          # the user's third back for CPU 9's second tight end: helps both
EVALUATE_BUDGET_SECONDS = 0.25
CHART_TAU = 40.0


def charted(know, changes: Mapping[int, dict] | None = None):
    """`know` with a plain chart: 1000 x exp(-(rank - 1) / 40) by healthy value (K and DST 15), times
    availability; no stars, no form. `changes` overrides fields per player."""
    skill = sorted((v for v in know.values if v.position in SKILL), key=lambda v: (-v.healthy_value, -v.ros_ppg,
                                                                                  v.player_id))
    rank = {v.player_id: n for n, v in enumerate(skill)}

    def one(v):
        healthy = round(1000 * math.exp(-rank[v.player_id] / CHART_TAU), 1) if v.player_id in rank else 15.0
        chart = round(healthy * v.availability, 1)
        fields = {"healthy_chart": healthy, "chart": chart, "pre_chart": healthy, "star": 0.0, "form_chart": 0.0,
                  **(changes or {}).get(v.player_id, {})}
        return replace(v, **fields)

    values = tuple(one(v) for v in know.values)
    return replace(know, values=values, by_id=MappingProxyType({v.player_id: v for v in values}))


def chart_knowledge(week: int = 1, settings: rs.Settings = SETTINGS, data=None, changes=None):
    return charted(fakes.fake_knowledge(week, settings, data), changes)


def league(week: int = 1, settings: rs.Settings = SETTINGS, changes=None):
    return replace(fakes.drafted_state(settings), week=week), chart_knowledge(week, settings, changes=changes)


def judge(to_team, give, get, drops=(), week=1, settings=SETTINGS, from_team=USER, state=None) -> rt.Verdict:
    drafted, know = league(week, settings)
    return rt.evaluate(state or drafted, settings, know, from_team, to_team, give, get, drops)


def as_user(state: rs.State, team_id: int) -> rs.State:
    """The same league with another team as the user's."""
    return replace(state, teams=tuple(replace(t, is_user=t.id == team_id) for t in state.teams))


def with_persona(state: rs.State, team_id: int, persona: str) -> rs.State:
    return rs.with_team(state, replace(rs.team(state, team_id), persona=persona))


def said(verdict: rt.Verdict, words: str) -> bool:
    return any(words in reason for reason in verdict.reasons)


def lock_free_agents(state: rs.State, know, position: str) -> rs.State:
    """Every free agent at `position` put on waivers, so nobody there can be picked up."""
    free = [v.player_id for v in know.values if v.position == position and v.player_id not in rs.rostered(state)]
    return replace(state, locked=(*state.locked, *free))


def star(chart: float, **more) -> dict:
    return {"chart": chart, "healthy_chart": chart, "pre_chart": chart, **more}


# ---------------- the two deals the owner named ----------------

ST_BROWN = P("WR", 2)                       # CPU 9's best receiver, made an ~800 star
PAIR = (P("RB", 10), P("WR", 16))           # the user's two good starters, made ~430 and ~370


@pytest.mark.parametrize("persona", PERSONAS)
def test_a_star_for_two_good_starters_is_declined_by_every_persona(persona):
    """Amon-Ra St. Brown for Tetairoa McMillan + Jordan Mason in 2025 week 1: the parts add up, the star wins."""
    changes = {ST_BROWN: star(800.0, star=1.0), PAIR[0]: star(430.0), PAIR[1]: star(370.0)}
    state, know = league(changes=changes)

    verdict = rt.evaluate(with_persona(state, NEEDS_RB, persona), SETTINGS, know, USER, NEEDS_RB, PAIR, [ST_BROWN])

    assert not verdict.accepted and verdict.score < 0 and said(verdict, rt.VALUE_REASON), verdict.reasons
    assert len(verdict.their_drops) == 1


@pytest.mark.parametrize("persona", PERSONAS)
def test_the_star_deal_is_declined_even_with_the_biggest_lineup_gain_and_nothing_to_cut(persona):
    know = SimpleNamespace(by_id={1: replace(fakes.fake_knowledge(1).values[0], player_id=1, **star(800.0, star=1.0)),
                                  2: replace(fakes.fake_knowledge(1).values[0], player_id=2, **star(430.0)),
                                  3: replace(fakes.fake_knowledge(1).values[0], player_id=3, **star(370.0))})

    counted = tv.tally(know, persona, [1], [2, 3], [], gain_per_week=100.0, multiple=1.0, to_user=True)

    assert counted.score < 0 and counted.premium < 0 and counted.lineup == pytest.approx(tv.LINEUP_CAP * 800)


def test_a_cpu_trades_its_only_tight_end_for_a_much_better_back_and_picks_one_up():
    """McBride for McCaffrey: it gives its only tight end; a free agent covers the slot."""
    only_te, back = P("TE", 25), P("RB", 10)
    state, know = league(changes={only_te: star(500.0), back: star(900.0, star=1.0)})

    verdict = rt.evaluate(state, SETTINGS, know, USER, DEEP_AT_WR, [back], [only_te])
    after, _ = rt.propose(state, SETTINGS, know, DEEP_AT_WR, [back], [only_te])
    no_cover = rt.evaluate(lock_free_agents(state, know, "TE"), SETTINGS, know, USER, DEEP_AT_WR, [back], [only_te])

    positions = [know.by_id[pid].position for pid in rs.team(after, DEEP_AT_WR).roster]
    assert verdict.accepted and verdict.score > 0 and "TE" not in positions
    assert rt.cover(know, SETTINGS, rs.team(after, DEEP_AT_WR).roster, rt.free_agents(after, know)) != ()
    assert not no_cover.accepted
    assert no_cover.reasons == ("It would leave CPU 5 without a full starting lineup (short at TE) and no free "
                                "agent to fill it.",)


def test_in_the_waivers_phase_a_slot_is_covered_by_anyone_the_user_has_not_claimed():
    """Everyone unrostered is on waivers then: the cover waits for them to clear (cpu_cover at start_week)."""
    only_te, back = P("TE", 25), P("RB", 10)
    state, know = league(changes={only_te: star(500.0), back: star(900.0, star=1.0)})
    waivers = lock_free_agents(replace(state, phase="waivers"), know, "TE")
    tes = [pid for pid in rt.free_agents(waivers, know) if know.by_id[pid].position == "TE"]
    claimed = replace(waivers, claims=tuple(rs.Claim(n, USER, pid, None) for n, pid in enumerate(tes, start=900)))

    assert tes and rt.evaluate(waivers, SETTINGS, know, USER, DEEP_AT_WR, [back], [only_te]).accepted
    assert not set(tes) & set(rt.free_agents(claimed, know))
    assert said(rt.evaluate(claimed, SETTINGS, know, USER, DEEP_AT_WR, [back], [only_te]), "short at TE")
    assert not set(tes) & set(rt.free_agents(replace(waivers, phase="open"), know))   # open: waivers are locked


# ---------------- value ----------------

def test_a_lopsided_one_for_one_is_declined_on_value_with_a_points_hint():
    verdict = judge(RICH, [SCRUB], [P("RB", 2)])

    assert not verdict.accepted and said(verdict, "Value is too far apart: CPU 2 would give")
    assert verdict.value_give < verdict.value_get and verdict.percent > 0.9 and verdict.fair_label == "big_win"
    assert verdict.give_values == ((SCRUB, verdict.value_give),) and verdict.get_values[0][0] == P("RB", 2)
    assert verdict.score < 0 and verdict.suggestion.startswith("Add about ")


def test_a_fair_deal_across_positions_is_accepted_and_carried_out():
    state, know = league()

    after, verdict = rt.propose(state, SETTINGS, know, *FAIR)

    assert verdict.accepted and verdict.reasons == () and verdict.score >= 0
    assert RB14 in rs.team(after, NEEDS_RB).roster and TE7 in rs.user_team(after).roster
    assert after.traded == {RB14: 1, TE7: 1}
    assert after.offers == (rs.Offer(id=state.next_id, week=1, from_team=USER, to_team=NEEDS_RB, give=(RB14,),
                                     get=(TE7,), status="accepted"),)
    event = after.events[-1]
    assert (event.kind, event.team_id, event.player_ids) == ("trade", USER, (RB14, TE7))
    assert event.text == "You traded Replay RB14 to CPU 9 for Replay TE7"
    assert RB14 in rs.user_team(state).roster and state.offers == ()         # the input is untouched
    assert rs.state_from_json(rs.to_json(after)) == after


def test_the_suggested_player_is_the_one_worth_closest_to_what_is_missing():
    state, know = league()

    verdict = judge(NEEDS_RB, [SCRUB], [P("RB", 36)])        # (CPU 5 has seven receivers: it would cut SCRUB)

    named = next(v for v in know.values if verdict.suggestion == f"Add {v.name} to make this fair.")
    assert -verdict.score <= named.chart <= rt.SUGGEST_WITHIN * -verdict.score
    assert named.player_id in rs.user_team(state).roster and named.position in SKILL


def test_hard_difficulty_needs_more_back_than_normal():
    hard = fakes.fake_settings(difficulty="hard")
    deal = (8, [RB14], [P("QB", 4)])                       # just about even on normal

    normal_verdict, hard_verdict = judge(*deal), judge(*deal, settings=hard)

    assert normal_verdict.accepted and not hard_verdict.accepted
    assert said(hard_verdict, "They want 11% more than they give (")              # 1.08 x the 3% margin


def test_a_value_decline_names_the_margin_and_a_lineup_loss():
    verdict = judge(RICH, [SCRUB], [P("RB", 2)])                         # a scrub for its starting back

    assert said(verdict, "They want 3% more than they give (")
    assert said(verdict, "It would weaken their starting lineup")
    assert not said(judge(8, [RB14], [P("QB", 4)], settings=fakes.fake_settings(difficulty="hard")),
                    "weaken their starting lineup")


def test_a_value_decline_adds_up_to_the_shortfall_the_suggestion_quotes():
    """Testers: "They want 3% more" beside a suggestion asking for 39-406 more; "add about 49" for a gap of 32."""
    for deal in ((RICH, [SCRUB], [P("RB", 2)]), (8, [RB14], [P("QB", 4)])):
        verdict = judge(*deal, settings=fakes.fake_settings(difficulty="hard"))
        sentence = next(r for r in verdict.reasons if r.startswith(rt.VALUE_REASONS))
        gives, gets = (int(re.search(rf"{word} (\d+)", sentence).group(1)) for word in ("give", "get"))
        parts = sum(int(n) for n in re.findall(r"\((\d+)\)", sentence))       # lineup loss, the share on top
        need = int(re.search(r"They need about (\d+) more", sentence).group(1))

        assert abs(gives + parts - gets - need) <= 2 and "-" not in sentence
        assert verdict.suggestion == f"Add about {need} points of value to make this fair." or verdict.suggestion.startswith("Add ")


def test_asking_again_gives_the_same_verdict_and_a_declined_proposal_is_kept_once():
    state, know = league()

    declined, verdict = rt.propose(state, SETTINGS, know, RICH, [SCRUB], [P("RB", 2)])
    rt.clear_cache()
    again, same_verdict = rt.propose(declined, SETTINGS, know, RICH, [SCRUB], [P("RB", 2)])

    assert not verdict.accepted and verdict == same_verdict and judge(*FAIR) == judge(*FAIR)
    assert [(o.status, o.from_team, o.to_team, o.give) for o in declined.offers] == [("declined", USER, RICH, (SCRUB,))]
    assert declined.teams == state.teams and declined.events == () and again is declined


def test_a_declined_proposal_resent_in_another_order_is_kept_once():
    state, know = league()
    give, get = [SCRUB, SCRUB3], list(rs.team(state, RICH).roster[:2])

    declined, _ = rt.propose(state, SETTINGS, know, RICH, give, get)
    again, _ = rt.propose(declined, SETTINGS, know, RICH, give[::-1], get[::-1])

    assert len(declined.offers) == 1 and again is declined


def test_kickers_and_defenses_count_for_little_and_cannot_balance_a_lopsided_deal():
    plain = judge(RICH, [SCRUB3], [P("WR", 11)])
    padded = judge(RICH, [SCRUB3, P("K", 5), P("DST", 8)], [P("WR", 11)])

    assert not plain.accepted and not padded.accepted and said(padded, rt.VALUE_REASON)
    assert dict(padded.give_values)[P("K", 5)] < plain.value_get / 10


def test_a_throw_in_the_cpu_would_cut_counts_for_nothing():
    alone = judge(8, [RB14], [P("QB", 4)])
    padded = judge(8, [RB14, SCRUB], [P("QB", 4)])

    assert padded.their_drops == (SCRUB,) and padded.score == alone.score


# ---------------- the scoring, on explicit values ----------------

def values(**charts: dict) -> SimpleNamespace:
    base = fakes.fake_knowledge(1).values[0]
    return SimpleNamespace(by_id={int(pid[1:]): replace(base, player_id=int(pid[1:]), **fields)
                                  for pid, fields in charts.items()})


def test_the_best_player_premium_spot_and_lineup_terms():
    know = values(p1=star(600.0), p2=star(300.0), p3=star(250.0))

    gets_best = tv.tally(know, "balanced", [2, 3], [1], [], 0.0, 1.0, True)
    sends_best = tv.tally(know, "balanced", [1], [2, 3], [], 0.0, 1.0, True)
    gambler = tv.tally(know, "gambler", [2, 3], [1], [], 0.0, 1.0, True)
    capped = tv.tally(know, "balanced", [2, 3], [1], [], -50.0, 1.0, True)

    assert gets_best.premium == pytest.approx(0.12 * 300) == -sends_best.premium
    assert gambler.premium == pytest.approx(0.18 * 300)
    assert gets_best.spot == tv.SPOT_VALUE and sends_best.spot == 0
    assert gets_best.score == pytest.approx(600 + 36 + 35 - 550 * 1.03)
    assert capped.lineup == pytest.approx(-tv.LINEUP_CAP * 550)


def test_a_shark_wants_an_edge_and_an_out_player_is_sold_to_the_user_at_a_floor():
    know = values(p1=star(500.0), p2=star(500.0), p3=star(200.0, healthy_chart=800.0, out=True))

    assert tv.tally(know, "balanced", [1], [2], [], 0.0, 1.0, False).score > tv.tally(
        know, "shark", [1], [2], [], 0.0, 1.0, False).score
    assert tv.tally(know, "balanced", [3], [1], [], 0.0, 1.0, True).sent == pytest.approx(tv.OUT_SELL_SHARE * 800)
    assert tv.tally(know, "balanced", [3], [1], [], 0.0, 0.95, False).sent == 200


def test_an_emotional_cpu_pays_more_for_a_hyped_player_and_asks_more_for_its_own():
    # 500, not 400: at 400 the hype makes him feel "high on" his player, which adds a mark-up (replay_styles)
    know = values(p1=star(500.0, shock_chart=30.0), p2=star(500.0))

    def score(sends, kept, hype):
        return tv.tally(know, "balanced", sends, kept, [], 0.0, 1.0, True, hype).score

    extra = (rp.HYPE - 1) * 30.0
    assert score([2], [1], rp.HYPE) == pytest.approx(score([2], [1], 1.0) + extra, abs=0.01)              # buying
    assert score([1], [2], rp.HYPE) == pytest.approx(score([1], [2], 1.0) - extra * tv.OUT_MARGIN, abs=0.01)  # holding


def test_evaluate_prices_a_hyped_player_with_the_receiving_cpus_hype_not_the_senders():
    to_team, give, get = FAIR
    state, know = league(changes={give[0]: {"shock_chart": 30.0}})        # the CPU receives a hyped back

    def score(team_id, hype):
        hyped = rs.with_team(state, replace(rs.team(state, team_id), hype=hype))
        return rt.evaluate(hyped, SETTINGS, know, USER, to_team, give, get).score

    calm = score(to_team, 1.0)
    assert score(to_team, rp.HYPE) == pytest.approx(calm + (rp.HYPE - 1) * 30.0, abs=0.05)
    assert score(USER, rp.HYPE) == pytest.approx(calm, abs=0.05)          # the user's own hype changes nothing


def test_the_market_guard_stops_a_persona_whose_private_values_say_yes():
    """A steady CPU marks down two players it never rated and up one it drafted high: never below 80% back."""
    know = values(p1=star(700.0, star=1.0, pre_chart=900.0), p2=star(500.0, star=1.0, pre_chart=100.0),
                  p3=star(460.0, star=1.0, pre_chart=100.0))

    counted = tv.tally(know, "steady", [2, 3], [1], [], 2.0, 1.0, True)

    assert counted.score >= 0 and not tv.market_ok(know, [2, 3], [1], [])
    assert tv.market_ok(know, [2], [1], [])


# ---------------- legal lineups ----------------

@pytest.mark.parametrize("position, only_one", [("TE", P("TE", 18)), ("K", P("K", 5)), ("DST", P("DST", 8))])
def test_a_side_left_short_needs_a_free_agent_who_can_fill_it(position, only_one):
    state, know = league()

    covered = rt.evaluate(state, SETTINGS, know, USER, DEEP_AT_WR, [only_one], [P("WR", 48)])
    stuck = rt.evaluate(lock_free_agents(state, know, position), SETTINGS, know, USER, DEEP_AT_WR, [only_one],
                        [P("WR", 48)])

    assert not said(covered, "full starting lineup")
    assert said(stuck, f"It would leave You without a full starting lineup (short at {position}) and no free agent")


def test_each_empty_slot_needs_its_own_free_agent():
    state, know = league()
    pool = rt.free_agents(state, know)
    first_te = next(pid for pid in pool if know.by_id[pid].position == "TE")
    no_te = [pid for pid in rs.team(state, DEEP_AT_WR).roster if know.by_id[pid].position != "TE"]

    assert rt.cover(know, SETTINGS, rs.team(state, DEEP_AT_WR).roster, pool) == ()
    assert rt.cover(know, SETTINGS, no_te, pool) == (first_te,)
    assert rt.cover(know, SETTINGS, no_te, pool, frozenset({first_te})) not in ((first_te,), None)
    assert rt.cover(know, SETTINGS, no_te, [first_te], frozenset({first_te})) is None
    assert all(pid not in rs.rostered(state) and not know.by_id[pid].out for pid in pool)


# ---------------- league rules ----------------

def test_a_traded_player_cannot_be_traded_again_for_two_weeks():
    state, know = league()
    traded, _ = rt.propose(state, SETTINGS, know, *FAIR)
    flip = (DEEP_AT_WR, [TE7], [P("WR", 44)])

    next_week = judge(*flip, week=2, state=replace(traded, week=2))
    week_after = judge(*flip, week=3, state=replace(traded, week=3))

    assert said(next_week, "Traded in the last 2 weeks, so not yet tradable again: Replay TE7.")
    assert not said(week_after, "Traded in the last")


def with_trades(state: rs.State, team_id: int, count: int) -> rs.State:
    """`count` more accepted trades this week for `team_id` (with a far-off CPU, moving nobody)."""
    other = next(t.id for t in state.teams if t.id not in (team_id, USER))
    made = tuple(rs.Offer(state.next_id + i, state.week, other, team_id, (), (), "accepted") for i in range(count))
    return replace(state, offers=(*state.offers, *made), next_id=state.next_id + count)


def test_a_cpu_makes_up_to_three_trades_a_week_but_the_user_may_deal_with_another_cpu():
    state, know = league()
    traded, verdict = rt.propose(state, SETTINGS, know, *FAIR)
    second = (NEEDS_RB, [RB23], [P("WR", 61)])

    assert verdict.accepted and rs.trades_this_week(traded, NEEDS_RB) == 1
    for already in (1, 2):
        assert not said(rt.evaluate(with_trades(traded, NEEDS_RB, already - 1), SETTINGS, know, USER, *second),
                        "trades this week")
    capped = with_trades(traded, NEEDS_RB, rs.MAX_TRADES_PER_WEEK - 1)
    same_cpu = rt.evaluate(capped, SETTINGS, know, USER, *second)
    other_cpu = rt.evaluate(capped, SETTINGS, know, USER, DEEP_AT_WR, [RB23], [P("WR", 44)])

    assert said(same_cpu, "CPU 9 has already made 3 trades this week.")
    assert not said(other_cpu, "trades this week")


def test_the_user_has_no_weekly_trade_cap():
    state, know = league()
    busy = with_trades(state, USER, rs.MAX_TRADES_PER_WEEK + 2)

    assert rs.trades_this_week(busy, USER) == 5
    assert rt.propose(busy, SETTINGS, know, *FAIR)[1].accepted


def test_trades_close_after_the_deadline_weeks_waivers():
    deadline = SETTINGS.trade_deadline_week
    waivers = replace(fakes.drafted_state(), week=deadline + 1, phase="waivers")
    no_waivers = fakes.fake_settings(waiver_mode="none")

    assert isinstance(judge(*FAIR, week=deadline), rt.Verdict)
    assert isinstance(judge(*FAIR, week=deadline + 1, state=waivers), rt.Verdict)
    with pytest.raises(ValueError, match="The trade deadline has passed: no trades after week 11."):
        judge(*FAIR, week=deadline + 1)
    with pytest.raises(ValueError, match="The trade deadline has passed"):
        judge(*FAIR, week=deadline + 1, settings=no_waivers,
              state=replace(fakes.drafted_state(no_waivers), week=deadline + 1, phase="waivers"))
    for phase in ("draft", "complete"):
        with pytest.raises(ValueError, match="Trades are open"):
            judge(*FAIR, state=replace(fakes.drafted_state(), phase=phase))


@pytest.mark.parametrize("to_team, give, get, message", [
    (NEEDS_RB, (RB14, SCRUB, SCRUB2, SCRUB3, P("WR", 41), P("WR", 40), P("WR", 39)), (TE7,), "at most 6 players on each side"),
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
    with pytest.raises(ValueError, match="doesn't need you to drop anyone"):
        judge(*FAIR, drops=[SCRUB])
    assert judge(*two_for_one, drops=[SCRUB]).your_drops == (SCRUB,)


def test_the_users_drop_goes_on_waivers_when_the_trade_goes_through():
    state, know = league()

    after, verdict = rt.propose(state, SETTINGS, know, NEEDS_RB, [RB14], [TE7, P("WR", 61)], [SCRUB])

    assert verdict.accepted, verdict.reasons
    assert after.locked == (SCRUB,) and SCRUB not in rs.rostered(after)
    assert [(e.kind, e.team_id, e.player_ids) for e in after.events[-2:]] == [
        ("trade", USER, (RB14, TE7, P("WR", 61))), ("drop", USER, (SCRUB,))]


def test_a_cpu_forced_to_drop_cuts_its_least_valuable_skill_player_and_never_its_kicker_or_defense():
    state, know = league()

    after, verdict = rt.propose(state, SETTINGS, know, NEEDS_RB, [RB14, SCRUB], [TE7])

    kept = [know.by_id[pid].position for pid in rs.team(after, NEEDS_RB).roster]
    assert verdict.accepted and verdict.their_drops == (SCRUB,), verdict.reasons
    assert len(kept) == SETTINGS.roster_size and {"K", "DST"} <= set(kept)
    assert set(verdict.their_drops) <= set(after.locked) and after.events[-1].kind == "drop"


# ---------------- answering a CPU's offer, and counters ----------------

def offered(give=(TE7,), get=(RB14,), from_team=NEEDS_RB, week=1, counter_to=None):
    """A league where a CPU has a pending offer out to the user: it sends `give` for the user's `get`."""
    state, know = league(week)
    return rt.record_offer(state, from_team, USER, give, get, "pending", counter_to), know


def test_accepting_an_offer_carries_it_out_under_the_same_offer():
    state, know = offered()
    offer = rt.offers_for_user(state)[0]

    after = rt.respond(state, SETTINGS, know, offer.id, accept=True)

    assert after.offers == (replace(offer, status="accepted"),) and rt.offers_for_user(after) == ()
    assert TE7 in rs.user_team(after).roster and RB14 in rs.team(after, NEEDS_RB).roster
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
    (lambda s: rs.with_team(s, replace(rs.team(s, RICH), roster=tuple(p for p in rs.team(s, RICH).roster
                                                                       if p != P("RB", 2)))),
     "Not on CPU 2's roster"),                                                     # the player is gone
    (lambda s: replace(s, week=2), "It was made in week 1."),                       # last week's offer
])
def test_an_offer_that_no_longer_stands_expires_with_the_reason(change, why):
    made, _ = offered(give=(P("RB", 2),), get=(SCRUB,), from_team=RICH)
    state = change(made)
    know = chart_knowledge(state.week)

    with pytest.raises(rt.OfferExpired, match=why) as error:
        rt.respond(state, SETTINGS, know, made.offers[0].id, accept=True)

    assert str(error.value).startswith("That offer no longer stands. ")
    assert error.value.state.offers[0].status == "expired" and state.offers[0].status == "pending"


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


def test_a_user_counter_marks_the_offer_countered_and_carries_counter_to():
    state, know = offered(give=(P("RB", 2),), get=(SCRUB,), from_team=RICH)
    offer_id = state.offers[0].id

    declined, verdict = rt.propose(state, SETTINGS, know, RICH, [SCRUB], [P("RB", 2)], counter_to=offer_id)
    accepted, fair = rt.propose(offered()[0], SETTINGS, know, NEEDS_RB, [RB14], [TE7], counter_to=offer_id)

    assert not verdict.accepted and [o.status for o in declined.offers] == ["countered", "declined"]
    assert declined.offers[-1].counter_to == offer_id
    assert fair.accepted and accepted.offers[0].status == "countered" and accepted.offers[-1].counter_to == offer_id
    with pytest.raises(ValueError, match="already countered"):
        rt.respond(declined, SETTINGS, know, offer_id, accept=True)
    with pytest.raises(ValueError, match="no such offer from that team"):
        rt.propose(state, SETTINGS, know, NEEDS_RB, [RB14], [TE7], counter_to=offer_id)


# ---------------- speed and hindsight ----------------

def test_evaluate_is_fast(own_cpu):
    state, know = league()
    rt.evaluate(state, SETTINGS, know, USER, RICH, [SCRUB], [P("RB", 2)])       # warms the shared projector
    rt.clear_cache()

    started = time.perf_counter()
    rt.evaluate(state, SETTINGS, know, USER, *FAIR)

    assert time.perf_counter() - started < EVALUATE_BUDGET_SECONDS


@pytest.mark.parametrize("week", [1, 6, 9])
def test_verdicts_do_not_change_when_the_future_does(week):
    state = replace(fakes.drafted_state(), week=week)
    changed = chart_knowledge(week, data=fakes.future_changed(fakes.fake_season(), after_week=week))
    deals = [(USER, *FAIR), (USER, RICH, (SCRUB,), (P("RB", 2),)), (USER, DEEP_AT_WR, (RB14, SCRUB), (P("WR", 22),)),
             (DEEP_AT_WR, RICH, (fakes.INJURED_STAR_ID,), (P("QB", 2),))]

    real = chart_knowledge(week)
    assert all(rt.evaluate(state, SETTINGS, changed, *d) == rt.evaluate(state, SETTINGS, real, *d) for d in deals)


def test_on_the_real_chart_the_star_for_two_deal_is_declined():
    """The fake season's own chart (replay_chart), not `charted`."""
    state, know = replace(fakes.drafted_state(), week=1), fakes.fake_knowledge(1)
    best = max((know.by_id[pid] for pid in rs.team(state, NEEDS_RB).roster), key=lambda v: v.chart)
    mine = sorted((know.by_id[pid] for pid in rs.user_team(state).roster if know.by_id[pid].chart < best.chart),
                  key=lambda v: -v.chart)
    pair = next((a, b) for a in mine for b in mine if a is not b and a.chart + b.chart <= 1.02 * best.chart
                and a.chart + b.chart >= 0.95 * best.chart and b.chart < a.chart)

    for persona in PERSONAS:
        verdict = rt.evaluate(with_persona(state, NEEDS_RB, persona), SETTINGS, know, USER, NEEDS_RB,
                              (pair[0].player_id, pair[1].player_id), [best.player_id])
        assert not verdict.accepted, (persona, verdict.reasons)


# ---------------- the IR slot ----------------

B1 = "can't add, claim or trade for players while"


def on_ir(state: rs.State, team_id: int, player_id: int) -> rs.State:
    team = rs.team(state, team_id)
    return rs.with_team(state, replace(team, roster=tuple(p for p in team.roster if p != player_id), ir=(player_id,)))


def hurt(week: int, *player_ids: int):
    """This week's knowledge with those players tagged O (so they may sit on IR without blocking anyone)."""
    tags = {(pid, week): fakes.Injury("O") for pid in player_ids}
    return chart_knowledge(week, data=fakes.tagged(fakes.fake_season(), tags))


def test_a_player_on_ir_may_be_traded_by_either_side():
    state, know = league()

    for moved, player in ((on_ir(state, USER, RB14), RB14), (on_ir(state, NEEDS_RB, TE7), TE7)):
        verdict = rt.evaluate(moved, SETTINGS, hurt(1, player), USER, *FAIR)
        assert not any("IR" in r for r in verdict.reasons), verdict.reasons


def test_a_traded_ir_player_takes_the_receivers_free_ir_slot_else_a_roster_spot():
    state = on_ir(league()[0], NEEDS_RB, TE7)
    user, cpu = rs.team(state, USER), rs.team(state, NEEDS_RB)

    assert rt._landing(user, cpu, (RB14,), (TE7,)) == (tuple(p for p in user.roster if p != RB14), (TE7,))
    assert rt._landing(cpu, user, (TE7,), (RB14,)) == ((*cpu.roster, RB14), ())
    full = replace(user, roster=tuple(p for p in user.roster if p != SCRUB), ir=(SCRUB,))
    assert rt._landing(full, cpu, (RB14,), (TE7,)) == ((*(p for p in full.roster if p != RB14), TE7), (SCRUB,))
    assert rt._landing(full, cpu, (SCRUB,), (TE7,)) == (full.roster, (TE7,))       # sending his IR player frees it


def test_an_accepted_trade_moves_the_ir_player_into_the_users_ir_slot():
    state = on_ir(league()[0], NEEDS_RB, TE7)
    know = hurt(1, TE7)
    verdict = rt.evaluate(state, SETTINGS, know, USER, *FAIR)

    after = rt._apply(state, SETTINGS, know, USER, NEEDS_RB, (RB14,), (TE7,), verdict)

    user, cpu = rs.team(after, USER), rs.team(after, NEEDS_RB)
    assert user.ir == (TE7,) and TE7 not in user.roster and RB14 not in user.roster
    assert cpu.ir == () and RB14 in cpu.roster
    assert verdict.your_drops == ()                     # he came in on IR: no roster spot used


def test_an_ir_player_arriving_with_the_ir_slot_full_needs_a_roster_spot():
    state = on_ir(league()[0], NEEDS_RB, TE7)
    stashed = rt.free_agents(state, league()[1])[0]
    know = hurt(1, TE7, stashed)
    deal = (USER, NEEDS_RB, [RB14], [TE7, P("WR", 61)])
    full = rs.with_team(state, replace(rs.team(state, USER), ir=(stashed,)))

    assert rt.evaluate(state, SETTINGS, know, *deal).your_drops == ()           # TE7 goes on the free IR slot
    with pytest.raises(ValueError, match="over the roster limit"):
        rt.evaluate(full, SETTINGS, know, *deal)


def test_the_value_hint_lands_the_users_ir_player_in_the_cpus_free_ir_slot(monkeypatch):
    """The suggestion prices the CPU's cuts on the roster the deal really leaves it (as _side does): the user's
    IR player takes its free IR slot instead of costing it a roster spot."""
    state = on_ir(league()[0], USER, RB14)
    seen = []
    real = rt._cpu_drops
    monkeypatch.setattr(rt, "_cpu_drops", lambda settings, know, kept, ir=(), before=(): (
        seen.append((tuple(kept), tuple(ir))) or real(settings, know, kept, ir, before)))

    verdict = rt.evaluate(state, SETTINGS, hurt(1, RB14), USER, RICH, [RB14], [P("RB", 2)])

    assert not verdict.accepted and verdict.suggestion
    assert len(seen) > 1 and all(RB14 in ir and RB14 not in kept for kept, ir in seen)


def test_the_roster_limit_of_a_trade_ignores_the_ir_player():
    state = on_ir(league()[0], USER, SCRUB)

    verdict = rt.evaluate(state, SETTINGS, hurt(1, SCRUB), USER, NEEDS_RB, [RB14], [TE7, P("WR", 61)])

    assert verdict.your_drops == ()


def test_a_blocked_user_cannot_propose_or_accept_but_may_decline():
    state, know = league()
    blocked = on_ir(state, USER, SCRUB)                     # no tag this week: he blocks the user
    made = on_ir(offered()[0], USER, SCRUB)

    told = f"^Activate {know.by_id[SCRUB].name} from IR to add, claim or trade"     # names him and the fix
    with pytest.raises(ValueError, match=told):
        rt.propose(blocked, SETTINGS, know, *FAIR)
    with pytest.raises(ValueError, match=told):
        rt.respond(made, SETTINGS, know, made.offers[0].id, accept=True)
    assert rt.evaluate(blocked, SETTINGS, know, USER, *FAIR).accepted       # asking is still allowed
    assert rt.respond(made, SETTINGS, know, made.offers[0].id, accept=False).offers[0].status == "declined"


def test_a_cpu_blocked_by_its_ir_declines_every_trade():
    state, know = league()
    healthy = min((pid for pid in rs.team(state, NEEDS_RB).roster if know.by_id[pid].position in SKILL
                   and pid not in FAIR[2]), key=lambda pid: (know.by_id[pid].chart, pid))
    blocked = on_ir(state, NEEDS_RB, healthy)

    verdict = rt.evaluate(blocked, SETTINGS, know, USER, *FAIR)

    assert not verdict.accepted and any(B1 in r and "CPU 9" in r for r in verdict.reasons)
    assert not said(rt.evaluate(blocked, SETTINGS, hurt(1, healthy), USER, *FAIR), B1)


# ---------------- many-for-few: depth is worth less than quality ----------------

def test_pieces_past_the_first_extra_one_count_at_the_depth_share():
    assert tv.depth_counted([100.0], [90.0]) == 100.0
    assert tv.depth_counted([300.0, 200.0], [450.0]) == 500.0                     # a plain 2-for-1 is untouched
    assert tv.depth_counted([50.0, 300.0, 200.0], [450.0]) == pytest.approx(500.0 + tv.DEPTH_SHARE * 50.0)
    assert tv.depth_counted([50.0, 300.0, 200.0], [450.0, 20.0]) == tv.depth_counted([50.0, 300.0, 200.0], [450.0])
    assert tv.depth_counted([50.0, 300.0, 200.0], [450.0, 200.0]) == 550.0        # a real second piece counts


@pytest.mark.parametrize("persona", PERSONAS)
def test_a_pile_of_depth_does_not_buy_a_starter_but_a_fair_two_for_one_still_does(persona):
    """Five bench pieces worth almost twice WR11 on the chart are declined; RB14 + WR31 for him is accepted."""
    state, know = league()
    state = with_persona(state, RICH, persona)
    pile = (P("QB", 8), P("WR", 31), RB23, P("RB", 31), SCRUB3)
    assert sum(know.by_id[pid].chart for pid in pile) > 1.8 * know.by_id[P("WR", 11)].chart

    many = rt.evaluate(state, SETTINGS, know, USER, RICH, pile, [P("WR", 11)])
    pair = rt.evaluate(state, SETTINGS, know, USER, RICH, (RB14, P("WR", 31)), [P("WR", 11)])

    assert not many.accepted and said(many, rt.VALUE_REASON), many.reasons
    assert many.suggestion == rt.DEPTH_HINT
    assert pair.accepted and pair.suggestion is None, (pair.reasons, pair.suggestion)


@pytest.mark.parametrize("persona", PERSONAS)
def test_asking_for_a_junk_throw_in_does_not_switch_the_depth_discount_off(persona):
    state, know = league()
    state = with_persona(state, RICH, persona)
    pile = (P("QB", 8), P("WR", 31), RB23, P("RB", 31), SCRUB3)

    for junk in ([P("WR", 59)], [P("WR", 59), P("RB", 44)], [P("WR", 59), P("RB", 44), P("RB", 40)]):
        verdict = rt.evaluate(state, SETTINGS, know, USER, RICH, pile, [P("WR", 11), *junk])
        assert not verdict.accepted, (junk, verdict.reasons)


def test_the_market_guard_counts_depth_at_the_discount():
    know = values(p1=star(500.0), p2=star(150.0), p3=star(150.0), p4=star(150.0), p5=star(150.0))

    assert tv.market_ok(know, [1], [2, 3, 4], []) is False         # 450 raw would pass at 80% of 500
    assert tv.market_ok(know, [1], [2, 3], []) is False and tv.market_ok(know, [2, 3], [1], []) is True


def test_a_suggested_player_really_makes_the_deal_go_through():
    """Even with a junk throw-in in what the CPU sends (no discount yet) and its own cuts to make room."""
    state, know = league()
    mine = [pid for pid in rs.team(state, USER).roster if know.by_id[pid].position in SKILL]
    names = {know.by_id[pid].name: pid for pid in mine}
    followed = 0
    for give in itertools.combinations(mine, 2):
        verdict = rt.evaluate(state, SETTINGS, know, USER, RICH, give, (P("WR", 11), P("RB", 44)))
        named = (verdict.suggestion or "").removeprefix("Add ").removesuffix(" to make this fair.")
        if verdict.accepted or named not in names:
            continue
        after = rt.evaluate(state, SETTINGS, know, USER, RICH, (*give, names[named]), (P("WR", 11), P("RB", 44)))
        assert after.accepted, (named, after.reasons)
        followed += 1
    assert followed >= 5


def test_a_suggestion_never_names_the_users_only_healthy_starter_at_a_position(monkeypatch):
    """Tester: "Add Patrick Mahomes" when Mahomes was the user's only quarterback."""
    state, know = league()
    user = rs.user_team(state)
    qb, backup = sorted((p for p in user.roster if know.by_id[p].position == "QB"), key=lambda p: -know.by_id[p].chart)
    alone = rs.with_team(state, replace(user, roster=tuple(p for p in user.roster if p != backup)))
    deal = (5, [SCRUB], [P("RB", 27)])

    with monkeypatch.context() as patched:                  # as before: any spare player could be named
        patched.setattr(rt.rv, "can_field_lineup", lambda *args: True)
        before = rt.evaluate(alone, SETTINGS, know, USER, *deal)
    now = rt.evaluate(alone, SETTINGS, know, USER, *deal)

    assert know.by_id[qb].name in (before.suggestion or "")
    assert know.by_id[qb].name not in (now.suggestion or "")


def test_after_the_deadline_a_blocked_user_is_told_about_the_deadline_not_his_ir():
    state, know = league()
    late = replace(on_ir(state, USER, SCRUB), week=SETTINGS.trade_deadline_week + 1)
    late_know = league(SETTINGS.trade_deadline_week + 1)[1]

    with pytest.raises(ValueError, match="^The trade deadline has passed"):
        rt.propose(late, SETTINGS, late_know, *FAIR)


def test_after_the_deadline_an_offer_cannot_be_accepted_and_stays_pending_but_can_be_declined():
    state, _ = offered()
    late_week = SETTINGS.trade_deadline_week + 1
    late, late_know = replace(state, week=late_week), league(late_week)[1]
    offer_id = late.offers[-1].id

    with pytest.raises(ValueError, match="^The trade deadline has passed"):
        rt.respond(late, SETTINGS, late_know, offer_id, accept=True)

    assert late.offers[-1].status == "pending"
    assert rt.respond(late, SETTINGS, late_know, offer_id, accept=False).offers[-1].status == "declined"
