"""Empirical score ranges and the correlated matchup simulation."""
from types import SimpleNamespace

import numpy as np
import pytest

from services import matchup_sim, score_ranges


def test_fit_keeps_zeros_shrinks_toward_the_median_and_needs_enough_games():
    pairs = [(10.0, r) for r in [0.0] * 20 + list(np.linspace(0.2, 2.5, 180))]
    fitted = score_ranges.fit({"RB": pairs, "QB": []})
    tier = fitted["RB"]["tiers"][0]
    assert (tier["min"], tier["max"], tier["n"]) == (8.0, 12.0, 200)
    q = tier["q"]
    assert q[0] == 0.0 and q == sorted(q) and len(q) == len(score_ranges.LEVELS)
    raw_q95 = float(np.percentile([r for _, r in pairs], 95))
    assert q[-1] < raw_q95 and "QB" not in fitted


def test_player_quantiles_use_the_tier_the_low_tier_fallback_and_the_shape(monkeypatch):
    monkeypatch.setattr(score_ranges, "stored", lambda: {"WR": {"all": [0.1, 0.2, 0.5, 1.0, 1.5, 2.0, 2.5], "n": 1,
                        "tiers": [{"min": 8.0, "max": 12.0, "q": [0.1, 0.25, 0.5, 0.9, 1.4, 2.0, 2.3], "n": 100}]}})
    q = score_ranges.player_quantiles("WR", 10.0)
    assert q[score_ranges.FLOOR_INDEX] == 2.5 and q[score_ranges.CEILING_INDEX] == 20.0
    assert score_ranges.player_quantiles("WR", 2.0)[:4] == (0.0, 0.0, 0.0, 0.0)     # low tier: often zero
    deep = score_ranges.player_quantiles("WR", 10.0, td_share=0.2, adot=12.0)        # deep targets
    td_heavy = score_ranges.player_quantiles("WR", 10.0, td_share=0.4, adot=7.0)
    assert deep[score_ranges.CEILING_INDEX] > q[score_ranges.CEILING_INDEX]
    assert td_heavy[score_ranges.FLOOR_INDEX] < q[score_ranges.FLOOR_INDEX]
    assert score_ranges.player_quantiles("K", 8.0) is None


def test_sampling_follows_the_quantiles():
    q = (0.0, 2.0, 5.0, 9.0, 13.0, 18.0, 21.0)
    draws = score_ranges.sample(q, np.random.default_rng(1).random(40_000))
    assert np.percentile(draws, 50) == pytest.approx(9.0, abs=0.3)
    assert np.percentile(draws, 90) == pytest.approx(18.0, abs=0.4)
    assert draws.min() >= 0.0


def _player(pid, position, team, opponent, mean=10.0, play=1.0, actual=None):
    q = score_ranges.normal_quantiles(mean, mean * 0.5, mean * 1.5)
    return SimpleNamespace(player_id=pid, position=position, team=team, matchup=SimpleNamespace(opponent=opponent),
                           projection=mean * play, if_active=mean, play_prob=play, floor=None, ceiling=None,
                           quantiles=q, actual=actual)


def test_correlations_follow_the_measured_pairs_and_stay_valid():
    qb, wr, opp_qb, dst = (_player(1, "QB", "KC", "BUF"), _player(2, "WR", "KC", "BUF"),
                           _player(3, "QB", "BUF", "KC"), _player(4, "DEF", "BUF", "KC"))
    assert matchup_sim.pair_correlation(qb, wr) == 0.32
    assert matchup_sim.pair_correlation(qb, opp_qb) == 0.17
    assert matchup_sim.pair_correlation(dst, qb) == -0.375                    # BUF defense vs KC's QB
    assert matchup_sim.pair_correlation(dst, opp_qb) == -0.07                 # same team
    matrix = matchup_sim.correlation_matrix([qb, wr, opp_qb, dst])
    assert np.allclose(np.diag(matrix), 1.0) and np.linalg.eigvalsh(matrix).min() > 0


def test_stacks_widen_the_spread_and_results_respect_sitting_and_final_games():
    rng = np.random.default_rng(3)
    stacked = matchup_sim.simulate_scores([_player(1, "QB", "KC", "BUF"), _player(2, "WR", "KC", "BUF")], rng, 30_000)
    apart = matchup_sim.simulate_scores([_player(1, "QB", "KC", "BUF"), _player(2, "WR", "DAL", "NYG")], rng, 30_000)
    assert stacked.sum(axis=1).std() > apart.sum(axis=1).std() * 1.05
    scores = matchup_sim.simulate_scores([_player(5, "RB", "KC", "BUF", play=0.0), _player(6, "RB", "KC", "BUF",
                                                                                           actual=17.5)], rng, 500)
    assert (scores[:, 0] == 0).all() and (scores[:, 1] == 17.5).all()
    assert matchup_sim.win_probability([_player(7, "RB", "A", "B", mean=25)], [_player(8, "RB", "C", "D", mean=8)]) > 0.9
