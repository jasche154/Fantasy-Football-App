"""Replay League trade chart (services/replay_chart.py): pure rank-to-points maths."""
from dataclasses import replace
from types import MappingProxyType

import pytest

from services import replay_chart as chart, replay_chart_injury, replay_news, replay_values
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


@pytest.mark.parametrize("week", fakes.INJURED_WEEKS)
def test_the_first_week_out_discounts_the_whole_chart_and_later_weeks_only_the_model_part(week):
    data, settings = fakes.fake_season(), fakes.fake_settings()
    star, know = fakes.INJURED_STAR_ID, fakes.fake_knowledge(week)
    base = replay_values._base(data.as_of(week), replay_values.league_settings(settings), week, settings.end_week)
    expert, model, _ = base.parts[star]
    value = know.by_id[star]
    games = [w for w in range(week, settings.end_week + 1) if data.schedule.opponent(value.team, w)]
    missed = len(games) * (1 - replay_news.share_played(value.news, games))
    discount = 1 - replay_chart_injury.discount(missed, len(games))          # the share of his chart he keeps

    assert expert > 0 and model > 0 and 0 < discount < 1 and value.news.since == fakes.INJURED_WEEKS[0]
    whole = round((expert + model) * discount, 1)
    assert value.chart == (whole if week == value.news.since else round(expert + model * discount, 1))
    assert week == value.news.since or value.chart > whole


def early_dst_season():
    """The fake season with real-world DST ADP: the first one at pick 92, one more every 4 picks."""
    data = fakes.fake_season()
    early = {pid: replace(p, adp=92.0 + 4 * (pid - fakes.player_id("DST", 1)))
             for pid, p in data.players.items() if p.position == "DST" and p.adp is not None}
    return replace(data, players=MappingProxyType({**data.players, **early}))


def test_kdst_slot_curve_starts_at_pick_150_and_skill_curves_are_unchanged():
    data = early_dst_season()

    curve, plain = chart.slot_curve(data), chart.slot_curve(fakes.fake_season())
    assert curve["DST"][0] >= chart.KDST_FIRST_PICK and list(curve["DST"]) == sorted(curve["DST"])
    assert all(curve[pos] == plain[pos] for pos in ("QB", "RB", "WR", "TE"))
    assert min(curve["K"]) >= chart.KDST_FIRST_PICK


def test_early_dst_adp_puts_dst1_behind_every_skill_player_drafted_before_150():
    data = replace(early_dst_season(), expert_ranks=MappingProxyType({}))     # ADP order only (2018-2019)
    ranks = chart.pre_ranks(chart.pre_picks(data))

    dst1 = min(ranks[pid] for pid, p in data.players.items() if p.position == "DST" and pid in ranks)
    skill = [ranks[pid] for pid, p in data.players.items()
             if p.position not in chart.KDST and p.adp is not None and p.adp < chart.KDST_FIRST_PICK]
    assert skill and dst1 > max(skill)
    assert chart.pick_value(chart.pre_picks(data)[fakes.player_id("DST", 1)]) < chart.pick_value(chart.KDST_FIRST_PICK - 1)


def test_no_chart_value_ever_tops_the_scale_even_after_the_bye_bump():
    """2026: Gibbs at 1030 (1000 x BYE_BUMP) on a scale documented as 0-1000."""
    top = max((v for week in range(1, 15) for v in fakes.fake_knowledge(week).values), key=lambda v: v.healthy_chart)
    assert top.healthy_chart == chart.CHART_MAX and top.chart <= chart.CHART_MAX


def _hot_streak_rise(data, monkeypatch, stand_in: bool) -> tuple[float, dict]:
    """How far a mid QB's chart rises when his last three games turn into 40-point games (week 6)."""
    monkeypatch.setattr(chart, "STAND_IN_EXPERTS", stand_in)
    week, settings = 6, fakes.fake_settings()
    seen = data.as_of(week)
    priors = replay_values._priors(seen)
    rows = [replay_values._row(seen, p, priors[pid], week, seen.team_at(pid, week), 10) for pid, p in seen.players.items()]
    qbs = sorted((r for r in rows if r["position"] == "QB"), key=lambda r: -r["ros_ppg"])
    mid = qbs[len(qbs) // 2]["player_id"]
    hot = [{**r, "recent_points": (40.0, 40.0, 40.0)} if r["player_id"] == mid else r for r in rows]
    before = chart.base_charts(seen, rows, priors, week, settings.end_week)[0]
    after = chart.base_charts(seen, hot, priors, week, settings.end_week)[0]
    return after[mid]["chart"] - before[mid]["chart"], after[mid]


def test_without_any_expert_ranks_the_sites_consensus_stands_in_and_damps_a_hot_streak(monkeypatch):
    """2018-19 have no expert ranks: the model alone swung QBs 124 -> 495 -> 633 in three weeks."""
    data = replace(fakes.fake_season(), expert_ranks=MappingProxyType({}))

    damped, shown = _hot_streak_rise(data, monkeypatch, True)
    wild, _ = _hot_streak_rise(data, monkeypatch, False)
    assert 0 < damped <= (1 - chart.EXPERT_WEIGHT) * wild + 1
    assert shown["expert_rank"] is None                     # nothing pretends to be an expert rank


def test_a_season_with_expert_ranks_never_uses_the_stand_in(monkeypatch):
    data = fakes.fake_season()
    assert _hot_streak_rise(data, monkeypatch, True) == _hot_streak_rise(data, monkeypatch, False)
