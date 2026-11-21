"""Replay League trade chart (services/replay_chart.py): pure rank-to-points maths."""
from dataclasses import replace
from types import MappingProxyType

import pytest

from services import replay_chart as chart
from services.replay_pool import PoolPlayer
from tests import replay_fakes as fakes

CURVE = {"RB": (1.0, 3.0, 7.0), "QB": (20.0,)}


@pytest.mark.parametrize("pick, points", [(1, 1000), (12, 760), (24, 563), (50, 294), (100, 84)])
def test_pick_value_follows_the_planned_curve(pick, points):
    assert chart.pick_value(pick) == pytest.approx(points, abs=1)


def test_pick_value_never_exceeds_the_top():
    assert chart.pick_value(0.2) == chart.CHART_MAX


def test_slot_curve_is_each_positions_adp_picks_in_order():
    data = fakes.fake_season()
    curve = chart.slot_curve(data)

    assert set(curve) == {"QB", "RB", "WR", "TE", "K", "DST"}
    assert all(list(picks) == sorted(picks) for picks in curve.values())
    assert sum(map(len, curve.values())) == sum(p.adp is not None for p in data.players.values())
    assert chart.slot_curve(data) is curve                                  # cached per season


def test_value_at_interpolates_fractional_ranks():
    assert chart.pick_at(CURVE, "RB", 1) == 1.0 and chart.pick_at(CURVE, "RB", 3) == 7.0
    assert chart.pick_at(CURVE, "RB", 1.5) == pytest.approx(2.0)
    assert chart.pick_at(CURVE, "RB", 2.25) == pytest.approx(4.0)
    assert chart.value_at(CURVE, "RB", 2.25) == pytest.approx(chart.pick_value(4.0))
    assert chart.pick_at(CURVE, "RB", 0.4) == 1.0                           # never better than the first


def test_past_the_last_drafted_each_rank_costs_three_picks():
    assert chart.pick_at(CURVE, "RB", 4) == 10.0 and chart.pick_at(CURVE, "RB", 5.5) == 14.5
    assert chart.pick_at(CURVE, "QB", 2) == 23.0
    assert chart.pick_at(CURVE, "K", 1) == chart.EMPTY_CURVE_START            # nobody drafted there
    assert chart.value_at(CURVE, "RB", 40) < chart.value_at(CURVE, "RB", 4)


def test_star_is_one_inside_the_top_12_and_zero_from_60():
    assert [chart.star(r) for r in (1, 12, 36, 60, 90, None)] == [1.0, 1.0, 0.5, 0.0, 0.0, 0.0]


def test_model_ppg_without_games_is_the_rate():
    assert chart.model_ppg(15.0, 15.0, (), 1.0) == 15.0


def test_a_stars_dip_counts_less_than_anyone_elses():
    star, other = chart.model_ppg(20.0, 20.0, (8, 10, 12), 1.0), chart.model_ppg(20.0, 20.0, (8, 10, 12), 0.0)

    assert other == pytest.approx(20 - 0.5 * 10) and star == pytest.approx(20 - 0.5 * 0.4 * 10)


def test_a_star_below_his_prior_bounces_back_faster():
    star, other = chart.model_ppg(12.0, 20.0, (20, 20, 20), 1.0), chart.model_ppg(12.0, 20.0, (20, 20, 20), 0.0)
    above_prior = chart.model_ppg(12.0, 10.0, (20, 20, 20), 1.0)

    assert other == pytest.approx(16.0) and star == pytest.approx(12 + 0.65 * 8) and above_prior == other


def test_fewer_games_move_the_rate_less_and_only_the_last_three_count():
    assert chart.model_ppg(10.0, 10.0, (16,), 0.0) == pytest.approx(10 + 0.5 / 3 * 6)
    assert chart.model_ppg(10.0, 10.0, (13, 13, 13, 100), 0.0) == pytest.approx(11.5)


def test_evenly_spaced_scores_get_whole_ranks():
    assert chart.fractional_ranks({1: 4.0, 2: 3.0, 3: 2.0, 4: 1.0}) == {1: 1.0, 2: 2.0, 3: 3.0, 4: 4.0}


def test_fractional_ranks_sit_between_the_neighbours_ranks():
    ranks = chart.fractional_ranks({1: 20.0, 2: 19.9, 3: 10.0, 4: 5.0})

    assert ranks[1] == 1.0 and ranks[4] == 4.0
    assert ranks[2] == pytest.approx(1 + 2 * 0.1 / 10.0)              # just below the top
    assert ranks[3] == pytest.approx(2 + 2 * 9.9 / 14.9)              # between 19.9 (rank 2) and 5 (rank 4)
    assert chart.fractional_ranks({}) == {} and chart.fractional_ranks({9: 3.0}) == {9: 1.0}


@pytest.mark.parametrize("tied", [2, 3, 5])
def test_tied_scores_share_their_first_rank_whatever_the_group_size(tied):
    scores = {0: 10.0, **{pid: 8.0 for pid in range(1, tied + 1)}, 99: 6.0}

    ranks = chart.fractional_ranks(scores)
    assert ranks[0] == 1.0 and {ranks[pid] for pid in range(1, tied + 1)} == {2.0} and ranks[99] == tied + 2
    assert set(chart.fractional_ranks({pid: 7.0 for pid in range(tied)}).values()) == {1.0}


def test_a_score_next_to_a_tie_sits_between_the_tie_and_its_other_neighbour():
    ranks = chart.fractional_ranks({1: 10.0, 2: 10.0, 3: 8.0, 4: 6.0})

    assert ranks[1] == ranks[2] == 1.0 and ranks[3] == pytest.approx(3.0) and ranks[4] == 4.0


def test_expert_weight_halves_per_week_past_fresh():
    assert chart.expert_weight(2) == chart.expert_weight(10) == chart.EXPERT_WEIGHT
    assert chart.expert_weight(17) == pytest.approx(chart.EXPERT_WEIGHT / 2)


def test_snapshot_falls_back_to_the_preseason_ranks_and_ages_them():
    data = fakes.fake_season()
    preseason_only = replace(data, expert_ranks=MappingProxyType({k: v for k, v in data.expert_ranks.items() if k[1] == 0}))

    ranks, weight = chart.snapshot(preseason_only, 3)
    assert ranks == chart.snapshot(preseason_only, 0)[0] and weight == pytest.approx(chart.expert_weight(3 + 14))
    assert chart.snapshot(data, 3)[1] == chart.EXPERT_WEIGHT
    assert chart.snapshot(replace(data, expert_ranks=MappingProxyType({})), 3) == ({}, 0.0)


def test_pre_ranks_interleave_expert_ranks_on_the_adp_curve():
    players = MappingProxyType({1: PoolPlayer(1, "A", "RB", 1.0), 2: PoolPlayer(2, "B", "RB", 3.0),
                                3: PoolPlayer(3, "C", "WR", 2.0), 4: PoolPlayer(4, "D", "WR", None)})
    data = replace(fakes.fake_season(), players=players,
                   expert_ranks=MappingProxyType({(2, 0): 1, (1, 0): 2, (3, 0): 1}))

    picks = chart.pre_picks(data)
    assert picks == {2: 1.0, 1: 3.0, 3: 2.0}                    # experts swap the backs; D has neither
    assert chart.pre_ranks(picks) == {2: 1, 3: 2, 1: 3}
