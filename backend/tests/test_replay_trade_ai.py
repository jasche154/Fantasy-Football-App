"""Replay League: the trades CPU managers make on their own (services/replay_trade_ai.py).

`seasons` plays cpu_trades for weeks 1-11 on fakes.drafted_state() for several seeds. Nothing else touches
the rosters, so every change in those states is a trade the CPUs made.
"""
import time
from dataclasses import dataclass, replace
from statistics import mean

import pytest

from services import replay_state as rs, replay_trade_ai as ai, replay_trades as rt, replay_values as rv
from tests import replay_fakes as fakes

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


def play(seed: int, settings: rs.Settings = SETTINGS) -> list[Week]:
    state, weeks = fakes.drafted_state(settings), []
    for week in WEEKS:
        before = replace(state, week=week)
        know = fakes.fake_knowledge(week, settings)
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

def test_cpus_make_a_handful_of_trades_a_season(seasons):
    counts = [len(trades(season)) for season in seasons.values()]

    assert all(1 <= count <= 12 for count in counts), counts
    assert 2 <= mean(counts) <= 8, counts


def test_every_cpu_trade_passes_the_acceptance_test_for_both_sides(seasons):
    for season in seasons.values():
        for week, offer in trades(season):
            know = fakes.fake_knowledge(week.week)

            verdict = rt.evaluate(week.before, SETTINGS, know, offer.from_team, offer.to_team, offer.give, offer.get)

            assert verdict.accepted, (week.week, offer, verdict.reasons)
            assert min(verdict.your_gain, verdict.their_gain) >= ai.MIN_CPU_GAIN > rt.MIN_TRADE_GAIN
            assert verdict.value_get >= verdict.value_give * rt.CPU_TRADE_MULTIPLE
            assert verdict.value_give >= verdict.value_get * rt.CPU_TRADE_MULTIPLE


def test_a_cpu_trades_at_most_once_a_week_and_never_moves_the_users_players(seasons):
    for season in seasons.values():
        for week in season:
            dealt = [team for offer in week.made if offer.status == "accepted"
                     for team in (offer.from_team, offer.to_team)]

            assert len(dealt) == len(set(dealt)) <= 2 * ai.CPU_ATTEMPTS and USER not in dealt
            assert rs.user_team(week.after).roster == rs.user_team(week.before).roster


def test_trades_are_carried_out_and_leave_every_roster_legal(seasons):
    for season in seasons.values():
        for week, offer in trades(season):
            sender, receiver = rs.team(week.after, offer.from_team), rs.team(week.after, offer.to_team)
            know = fakes.fake_knowledge(week.week)

            assert set(offer.get) <= set(sender.roster) and set(offer.give) <= set(receiver.roster)
            assert all(week.after.traded[pid] == week.week for pid in (*offer.give, *offer.get))
            assert max(len(offer.give), len(offer.get)) <= max(ai.PACKAGE_SIZES)
            for team in (sender, receiver):
                assert len(team.roster) <= SETTINGS.roster_size
                assert rv.can_field_lineup([know.by_id[pid].position for pid in team.roster], SETTINGS)
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
            know = fakes.fake_knowledge(week.week)

            verdict = rt.evaluate(week.after, SETTINGS, know, USER, offer.from_team, offer.get, offer.give)

            assert offer.to_team == USER and offer.week == week.week
            assert verdict.accepted and verdict.your_drops == (), (week.week, offer, verdict.reasons)
            assert verdict.their_gain >= ai.MIN_CPU_GAIN
            assert ai.fair_for_user(SETTINGS, know, verdict.value_give, verdict.value_get)
            assert not verdict.fair_label.endswith("loss")
            assert not any(know.by_id[pid].out for pid in (*offer.give, *offer.get))
    assert sum(len(offers(season)) for season in seasons.values()) >= len(SEEDS)


def test_an_offer_moves_nobody_until_the_user_accepts_it(seasons):
    week, offer = next(found for season in seasons.values() for found in offers(season))
    know = fakes.fake_knowledge(week.week)

    accepted = rt.respond(week.after, SETTINGS, know, offer.id, accept=True)

    assert set(offer.give) <= set(rs.team(week.after, offer.from_team).roster)       # still the CPU's
    assert set(offer.get) <= set(rs.user_team(week.after).roster)
    assert set(offer.give) <= set(rs.user_team(accepted).roster)
    assert set(offer.get) <= set(rs.team(accepted, offer.from_team).roster)


def test_a_second_run_in_the_same_week_does_not_send_a_second_offer(seasons):
    seed, (week, _) = next((seed, found) for seed, season in seasons.items() for found in offers(season))
    know = fakes.fake_knowledge(week.week)

    again = ai.cpu_trades(week.after, SETTINGS, know, seed)

    assert sum(o.to_team == USER and o.week == week.week for o in again.offers) == 1


def test_players_who_are_out_are_kept_out_of_the_search_for_the_user_only():
    state = replace(fakes.drafted_state(), week=6)
    know = fakes.fake_knowledge(6)
    owner = rs.team(state, STAR_OWNER)

    for_cpu = [v.player_id for v in ai._tradable(state, know, owner, with_user=False)]
    for_user = [v.player_id for v in ai._tradable(state, know, owner, with_user=True)]

    assert fakes.INJURED_STAR_ID in for_cpu and fakes.INJURED_STAR_ID not in for_user
    assert len(for_cpu) == ai.TOP_SKILL


def test_on_hard_an_offer_still_gives_the_cpu_its_premium():
    hard = fakes.fake_settings(difficulty="hard")
    made = [(week, offer) for seed in range(3) for week, offer in offers(play(seed, hard))]

    assert made
    for week, offer in made:
        know = fakes.fake_knowledge(week.week, hard)
        verdict = rt.evaluate(week.after, hard, know, USER, offer.from_team, offer.get, offer.give)
        assert verdict.accepted and verdict.value_give >= verdict.value_get * rs.USER_TRADE_MULTIPLE["hard"]
        assert ai.fair_for_user(hard, know, verdict.value_give, verdict.value_get)


# ---------------- determinism, bounds, speed ----------------

def test_the_same_seed_gives_the_same_trades_and_another_seed_different_ones(seasons):
    replayed = play(0)

    assert rs.to_json(replayed[-1].after) == rs.to_json(seasons[0][-1].after)
    assert len({rs.to_json(season[-1].after) for season in seasons.values()}) > 1


def test_nothing_happens_after_the_deadline_or_outside_the_trading_phases():
    late = replace(fakes.drafted_state(), week=SETTINGS.trade_deadline_week + 1)
    know = fakes.fake_knowledge(late.week)

    assert ai.cpu_trades(late, SETTINGS, know, 0) is late
    for phase in ("draft", "complete"):
        closed = replace(fakes.drafted_state(), phase=phase)
        assert ai.cpu_trades(closed, SETTINGS, fakes.fake_knowledge(1), 0) is closed


def test_the_search_is_bounded(monkeypatch):
    state, know, calls = fakes.drafted_state(), fakes.fake_knowledge(1), []
    evaluate = rt.evaluate
    monkeypatch.setattr(rt, "evaluate", lambda *args, **kwargs: calls.append(args) or evaluate(*args, **kwargs))

    for seed in SEEDS:
        ai.cpu_trades(state, SETTINGS, know, seed)

    searches = ai.CPU_ATTEMPTS + 1                       # plus one for the offer to the user
    assert 0 < len(calls) <= len(SEEDS) * searches * (ai.MAX_SCORED + 1)       # + 1: execute checks again


def test_cpu_trades_stays_within_its_time_budget(seasons):
    slowest = max(week.seconds for season in seasons.values() for week in season)

    assert slowest < BUDGET_SECONDS, slowest


# ---------------- no hindsight ----------------

@pytest.mark.parametrize("week", [1, 4, 7, 10])
def test_cpu_trades_are_identical_when_the_future_is_different(seasons, week):
    changed = fakes.future_changed(fakes.fake_season(), after_week=week)
    know = fakes.fake_knowledge(week, data=changed)

    for seed, season in seasons.items():
        played = season[week - 1]

        assert rs.to_json(ai.cpu_trades(played.before, SETTINGS, know, seed)) == rs.to_json(played.after)
