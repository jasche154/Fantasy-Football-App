"""Replay League: the trades CPU managers make on their own (services/replay_trade_ai.py).

`seasons` plays cpu_trades for weeks 1-11 on fakes.drafted_state() for several seeds, with personas from
replay_personas.assign and the plain chart of test_replay_trades (independent of replay_chart). Nothing else
touches the rosters, so every change in those states is a trade the CPUs made.
"""
import sys
import time
from collections import Counter
from dataclasses import dataclass, replace
from statistics import mean

import pytest

from services import replay_personas, replay_state as rs, replay_trade_ai as ai, replay_trades as rt
from tests import replay_fakes as fakes
from tests.test_replay_trades import chart_knowledge

P = fakes.player_id
SETTINGS = fakes.fake_settings()
SEEDS = range(6)
WEEKS = range(1, SETTINGS.trade_deadline_week + 1)
USER = SETTINGS.draft_slot
BUDGET_SECONDS = 0.5
STAR_OWNER = 5                              # the CPU that drafted fakes.INJURED_STAR_ID


@dataclass(frozen=True)
class Week:
    week: int
    before: rs.State
    after: rs.State
    seconds: float

    @property
    def made(self) -> list[rs.Offer]:
        """The offers cpu_trades added this week: accepted CPU-CPU trades and a pending one to the user."""
        return list(self.after.offers[len(self.before.offers):])


def with_personas(state: rs.State, seed: int) -> rs.State:
    picked = replay_personas.assign(seed, [t.id for t in state.teams if not t.is_user])
    return replace(state, teams=tuple(replace(t, persona=picked.get(t.id, t.persona)) for t in state.teams))


def knowledge(week: int, settings: rs.Settings = SETTINGS, data=None):
    return chart_knowledge(week, settings, data)


def play(seed: int, settings: rs.Settings = SETTINGS) -> list[Week]:
    state, weeks = with_personas(fakes.drafted_state(settings), seed), []
    for week in WEEKS:
        before = replace(state, week=week)
        know = knowledge(week, settings)
        started = time.perf_counter()
        state = ai.cpu_trades(before, settings, know, seed)
        weeks.append(Week(week, before, state, time.perf_counter() - started))
    return weeks


@pytest.fixture(scope="module")
def seasons() -> dict[int, list[Week]]:
    return {seed: play(seed) for seed in SEEDS}


def trades(season: list[Week]) -> list[tuple[Week, rs.Offer]]:
    return [(week, offer) for week in season for offer in week.made if offer.status == "accepted"]


def offers(season: list[Week]) -> list[tuple[Week, rs.Offer]]:
    return [(week, offer) for week in season for offer in week.made if offer.status == "pending"]


# ---------------- CPU-CPU trades ----------------

def test_cpus_trade_every_season_without_a_frenzy(seasons):
    """This league is a straight-ADP draft (teams with four quarterbacks next to teams with one) and nothing
    but trades touches it, so deals are easy to find."""
    counts = [len(trades(season)) for season in seasons.values()]

    assert all(3 <= count <= 12 for count in counts), counts
    assert 5 <= mean(counts) <= 10, counts


def test_an_attempt_asks_several_pairs_and_stops_at_the_first_deal(monkeypatch):
    state, know, asked = fakes.drafted_state(), knowledge(1), []
    best_deal = ai._best_deal

    def no_deal(state, settings, know, first, second, scored):
        asked.append((first.id, second.id))

    def recorded(state, settings, know, first, second, scored):
        asked.append((first.id, second.id))
        return best_deal(state, settings, know, first, second, scored) if len(asked) > 1 else None

    monkeypatch.setattr(ai, "_best_deal", no_deal)
    unchanged = ai._cpu_attempt(state, SETTINGS, know, rs.rng(0, "pairs"))
    without_luck = list(asked)
    asked.clear()
    monkeypatch.setattr(ai, "_best_deal", recorded)
    traded = [ai._cpu_attempt(state, SETTINGS, know, rs.rng(seed, "pairs")) for seed in SEEDS]

    assert unchanged is state and len(set(without_luck)) == len(without_luck) == ai.MAX_PAIRS > 1
    assert all(USER not in pair and pair[0] != pair[1] for pair in without_luck)
    assert any(len(after.offers) == 1 for after in traded)   # the first pair said no, a later one dealt
    assert len(asked) <= len(SEEDS) * ai.MAX_PAIRS


def test_every_cpu_trade_leaves_each_roster_able_to_fill_its_lineup(seasons):
    """Positions need not match; a slot the deal empties must have its own free agent to fill it."""
    for season in seasons.values():
        for week, offer in trades(season):
            know = knowledge(week.week)
            pool = rt.free_agents(week.after, know)
            for team_id in (offer.from_team, offer.to_team):
                assert rt.cover(know, SETTINGS, rs.team(week.after, team_id).roster, pool) is not None


def test_the_test_suite_clears_the_trade_and_draft_caches():
    from services import replay_draft
    from tests.conftest import _clear_caches

    rt._season(SETTINGS, knowledge(1), rs.user_team(fakes.drafted_state()).roster)
    replay_draft._board(fakes.fake_season().as_of(0))
    assert rt._seasons.cache_info().currsize and replay_draft._board.cache_info().currsize

    _clear_caches()

    assert rt._seasons.cache_info().currsize == replay_draft._board.cache_info().currsize == 0


def test_every_cpu_trade_passes_the_acceptance_test_for_both_sides(seasons):
    for season in seasons.values():
        for week, offer in trades(season):
            know = knowledge(week.week)

            verdict = rt.evaluate(week.before, SETTINGS, know, offer.from_team, offer.to_team, offer.give, offer.get)

            assert verdict.accepted and verdict.score >= 0, (week.week, offer, verdict.reasons)
            assert verdict.value_get >= verdict.value_give * 0.8 and verdict.value_give >= verdict.value_get * 0.8


def test_a_cpu_trades_at_most_the_cap_a_week_and_never_moves_the_users_players(seasons):
    for season in seasons.values():
        for week in season:
            dealt = Counter(team for offer in week.made if offer.status == "accepted"
                            for team in (offer.from_team, offer.to_team))

            assert sum(dealt.values()) <= 2 * ai.CPU_ATTEMPTS and USER not in dealt
            assert max(dealt.values(), default=0) <= rs.MAX_TRADES_PER_WEEK
            assert rs.user_team(week.after).roster == rs.user_team(week.before).roster


def test_a_cpu_that_traded_this_week_may_trade_again_until_the_cap(monkeypatch):
    """Every pair finds a deal (stubbed) and every attempt runs: with three CPUs each deals more than once,
    but none past the cap, and once all are capped the attempts stop dealing."""
    monkeypatch.setattr(ai, "EXTRA_ATTEMPT_CHANCE", 1.0)
    monkeypatch.setattr(ai, "CPU_ATTEMPTS", 10)
    monkeypatch.setattr(ai, "_best_deal", lambda *args: ((), ()))
    monkeypatch.setattr(ai.rt, "execute", lambda state, settings, know, first, second, give, get:
                        rt.record_offer(state, first, second, give, get, "accepted"))
    monkeypatch.setattr(ai, "_user_offer", lambda state, *args: state)
    state = fakes.drafted_state()
    cpus = [t.id for t in state.teams if not t.is_user][:3]
    few = replace(state, teams=tuple(t for t in state.teams if t.is_user or t.id in cpus))

    after = ai.cpu_trades(few, SETTINGS, knowledge(1), seed=0)

    dealt = Counter(team for offer in after.offers for team in (offer.from_team, offer.to_team))
    assert set(dealt) <= set(cpus) and len(after.offers) == 4          # 3 CPUs x a cap of 3, 2 a deal
    assert all(1 < count <= rs.MAX_TRADES_PER_WEEK for count in dealt.values())
    assert len(ai._free_cpus(after)) == 1 and ai.cpu_trades(few, SETTINGS, knowledge(1), seed=0) == after


def test_trades_are_carried_out_and_leave_every_roster_legal(seasons):
    for season in seasons.values():
        for week, offer in trades(season):
            sender, receiver = rs.team(week.after, offer.from_team), rs.team(week.after, offer.to_team)

            assert set(offer.get) <= set(sender.roster) and set(offer.give) <= set(receiver.roster)
            assert all(week.after.traded[pid] == week.week for pid in (*offer.give, *offer.get))
            assert max(len(offer.give), len(offer.get)) <= max(ai.PACKAGE_SIZES)
            for team in (sender, receiver):
                assert len(team.roster) <= SETTINGS.roster_size
        events = [event for event in season[-1].after.events if event.kind == "trade"]
        assert len(events) == len(trades(season))


def test_a_traded_player_is_not_traded_again_within_two_weeks(seasons):
    for season in seasons.values():
        last: dict[int, int] = {}
        for week, offer in trades(season):
            for pid in (*offer.give, *offer.get):
                assert week.week - last.get(pid, -rs.RETRADE_LOCK_WEEKS) >= rs.RETRADE_LOCK_WEEKS
                last[pid] = week.week


# ---------------- offers to the user ----------------

def test_the_user_gets_at_most_one_offer_a_week_and_each_passes_the_cpus_own_test(seasons):
    for season in seasons.values():
        for week in season:
            assert sum(offer.status == "pending" for offer in week.made) <= 1
        for week, offer in offers(season):
            know = knowledge(week.week)

            verdict = rt.evaluate(week.after, SETTINGS, know, USER, offer.from_team, offer.get, offer.give)

            assert offer.to_team == USER and offer.week == week.week
            assert verdict.accepted and verdict.your_drops == (), (week.week, offer, verdict.reasons)
            assert ai.fair_for_user(SETTINGS, know, verdict.value_give, verdict.value_get)
            assert not verdict.fair_label.endswith("loss")
    assert sum(len(offers(season)) for season in seasons.values()) >= len(SEEDS)


def test_an_offer_moves_nobody_until_the_user_accepts_it(seasons):
    week, offer = next(found for season in seasons.values() for found in offers(season))
    know = knowledge(week.week)

    accepted = rt.respond(week.after, SETTINGS, know, offer.id, accept=True)

    assert set(offer.give) <= set(rs.team(week.after, offer.from_team).roster)       # still the CPU's
    assert set(offer.get) <= set(rs.user_team(week.after).roster)
    assert set(offer.give) <= set(rs.user_team(accepted).roster)
    assert set(offer.get) <= set(rs.team(accepted, offer.from_team).roster)


def test_a_second_run_in_the_same_week_does_not_send_a_second_offer(seasons):
    seed, (week, _) = next((seed, found) for seed, season in seasons.items() for found in offers(season))
    know = knowledge(week.week)

    again = ai.cpu_trades(week.after, SETTINGS, know, seed)

    assert sum(o.to_team == USER and o.week == week.week for o in again.offers) == 1


def test_an_out_player_on_the_active_roster_is_in_the_search():
    state = replace(fakes.drafted_state(), week=6)
    know = knowledge(6)
    owner, star = rs.team(state, STAR_OWNER), fakes.INJURED_STAR_ID

    tradable = [v.player_id for v in ai._tradable(state, know, owner)]

    assert know.by_id[star].out and star in tradable and len(tradable) == ai.TOP_SKILL
    assert ai._packages([know.by_id[star]]) == [(know.by_id[star].chart, (star,))]


def test_a_shark_looks_first_at_slumping_stars_and_sells_its_hot_players():
    state = fakes.drafted_state()
    user = rs.user_team(state)
    slumping, hot = P("RB", 23), P("RB", 31)
    know = chart_knowledge(1, changes={slumping: {"star": 0.9, "form_chart": -150.0},
                                       hot: {"form_chart": 120.0}})

    assert ai._tradable(state, know, user, "buy")[0].player_id == slumping
    assert ai._tradable(state, know, user, "sell")[0].player_id == hot
    assert ai._tradable(state, know, user)[0].player_id == P("RB", 1)


def test_sharks_send_the_user_the_most_offers(seasons):
    sent = Counter(seasons[seed][0].before.teams[offer.from_team - 1].persona
                   for seed in SEEDS for _, offer in offers(seasons[seed]))
    sharks = Counter(t.persona for seed in SEEDS for t in seasons[seed][0].before.teams if not t.is_user)

    per_team = {persona: sent[persona] / sharks[persona] for persona in sharks}
    assert per_team["shark"] == max(per_team.values()), per_team


def test_cpu_trades_can_move_a_player_who_is_out(monkeypatch):
    week, star = fakes.INJURED_WEEKS[-1], fakes.INJURED_STAR_ID
    state, know = replace(fakes.drafted_state(), week=week), knowledge(week)
    candidates = ai._candidates
    monkeypatch.setattr(ai, "_candidates", lambda *args: [deal for deal in candidates(*args) if star in (*deal[0],
                                                                                                         *deal[1])])
    changed = knowledge(week, data=fakes.future_changed(fakes.fake_season(), after_week=week))

    after = [ai.cpu_trades(state, SETTINGS, know, seed) for seed in range(20)]
    moved = [made for made in after if star not in rs.team(made, STAR_OWNER).roster]

    assert know.by_id[star].out and moved
    assert [rs.to_json(ai.cpu_trades(state, SETTINGS, changed, seed)) for seed in range(20)] == list(
        map(rs.to_json, after))


def test_on_hard_an_offer_still_gives_the_cpu_its_premium():
    hard = fakes.fake_settings(difficulty="hard")
    made = [(week, offer) for seed in range(3) for week, offer in offers(play(seed, hard))]

    assert made
    for week, offer in made:
        know = knowledge(week.week, hard)
        verdict = rt.evaluate(week.after, hard, know, USER, offer.from_team, offer.get, offer.give)
        assert verdict.accepted and verdict.score >= 0
        assert ai.fair_for_user(hard, know, verdict.value_give, verdict.value_get)


# ---------------- determinism, bounds, speed ----------------

def test_the_same_seed_gives_the_same_trades_and_another_seed_different_ones(seasons):
    replayed = play(0)

    assert rs.to_json(replayed[-1].after) == rs.to_json(seasons[0][-1].after)
    assert len({rs.to_json(season[-1].after) for season in seasons.values()}) > 1


def test_nothing_happens_after_the_deadline_or_outside_the_trading_phases():
    late = replace(fakes.drafted_state(), week=SETTINGS.trade_deadline_week + 1)
    know = knowledge(late.week)

    assert ai.cpu_trades(late, SETTINGS, know, 0) is late
    for phase in ("draft", "complete"):
        closed = replace(fakes.drafted_state(), phase=phase)
        assert ai.cpu_trades(closed, SETTINGS, knowledge(1), 0) is closed


def test_the_search_is_bounded(monkeypatch):
    state, know, calls = fakes.drafted_state(), knowledge(1), []
    evaluate = rt.evaluate
    monkeypatch.setattr(rt, "evaluate", lambda *args, **kwargs: calls.append(args) or evaluate(*args, **kwargs))

    for seed in SEEDS:
        ai.cpu_trades(state, SETTINGS, know, seed)

    searches = ai.CPU_ATTEMPTS * ai.MAX_PAIRS + 1        # plus one for the offer to the user
    assert 0 < len(calls) <= len(SEEDS) * searches * (ai.MAX_SCORED + 1)       # + 1: execute checks again


def test_a_player_on_ir_is_never_searched_or_offered(monkeypatch):
    """Every team's two most valuable skill players go on IR (no tag, so nothing else excludes them)."""
    state, know = fakes.drafted_state(), knowledge(1)
    for team in state.teams:
        best = [v.player_id for v in ai._tradable(state, know, team)[:2]]
        state = rs.with_team(state, replace(team, roster=tuple(p for p in team.roster if p not in best), ir=tuple(best)))
    on_ir = {pid for team in state.teams for pid in team.ir}
    searched = []
    tradable = ai._tradable
    monkeypatch.setattr(ai, "_tradable", lambda *args: searched.append(found := tradable(*args)) or found)
    monkeypatch.setattr(rt.replay_ir, "blocked_reason", lambda *args: None)     # untagged on IR would block every CPU

    after = [ai.cpu_trades(state, SETTINGS, know, seed) for seed in SEEDS]

    dealt = {pid for later in after for offer in later.offers for pid in (*offer.give, *offer.get)}
    assert len(on_ir) == 2 * SETTINGS.teams and searched and dealt
    assert not on_ir & {v.player_id for found in searched for v in found} and not on_ir & dealt


def test_cpu_trades_stays_within_its_time_budget(seasons):
    # A coverage tracer makes this pure-Python loop about 4x slower, so the wall-clock budget only means something without one.
    monitoring = getattr(sys, "monitoring", None)       # Python 3.12+: coverage may use it instead of a trace function
    if sys.gettrace() is not None or (monitoring and monitoring.get_tool(monitoring.COVERAGE_ID) is not None):
        pytest.skip("timing is not meaningful under a tracer")
    slowest = max(week.seconds for season in seasons.values() for week in season)

    assert slowest < BUDGET_SECONDS, slowest


# ---------------- no hindsight ----------------

@pytest.mark.parametrize("week", [1, 4, 7, 10])
def test_cpu_trades_are_identical_when_the_future_is_different(seasons, week):
    changed = fakes.future_changed(fakes.fake_season(), after_week=week)
    know = knowledge(week, data=changed)

    for seed, season in seasons.items():
        played = season[week - 1]

        assert rs.to_json(ai.cpu_trades(played.before, SETTINGS, know, seed)) == rs.to_json(played.after)
