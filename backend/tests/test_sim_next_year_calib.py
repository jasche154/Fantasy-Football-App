"""sim_next_year_calib: the whole-season fits (efficiency scale, QB benchings, rookie growth) on synthetic data."""
from types import MappingProxyType, SimpleNamespace

import numpy as np
import pytest

from services import sim_metrics, sim_next_year_calib as calib
from services.replay_pool import Injury
from tests import replay_fakes as fakes


def test_the_scale_with_the_least_error_wins_per_position_over_the_given_seasons():
    by_scale = {0.0: {2021: {"QB": (5.0, 10), "WR": (1.0, 10)}, 2022: {"QB": (5.0, 10), "WR": (9.0, 10)}},
                1.0: {2021: {"QB": (1.0, 10), "WR": (3.0, 10)}, 2022: {"QB": (1.0, 10), "WR": (3.0, 10)}}}
    assert calib.best_scale(by_scale, [2021, 2022])["QB"] == 1.0
    assert calib.best_scale(by_scale, [2021, 2022])["WR"] == 1.0
    assert calib.best_scale(by_scale, [2021])["WR"] == 0.0          # leaving 2022 out changes the answer


def test_the_logistic_recovers_a_known_curve():
    dice = np.random.default_rng(0)
    a, b = -4.0, 0.1
    rows = [(2020, int(rank), bool(dice.random() < 1 / (1 + np.exp(-(a + b * rank)))))
            for rank in dice.integers(1, 33, 20000)]
    fit = calib.logistic(rows)
    assert fit["logit"] == pytest.approx([a, b], abs=0.15) and fit["n"] == 20000


def test_a_starter_loses_the_job_only_when_healthy_and_for_good():
    weeks = list(range(1, 18))
    real = SimpleNamespace(last_week=18, injuries=MappingProxyType({}))
    benched = {("KC", w): 1 if w < 6 else 2 for w in weeks}
    assert calib._lost(real, benched, "KC", 1, weeks)
    hurt = SimpleNamespace(last_week=18, injuries=MappingProxyType({(1, 7): Injury("O")}))
    assert not calib._lost(hurt, benched, "KC", 1, weeks)            # tagged: an injury, not a benching
    back = {("KC", w): 2 if w in (6, 7) else 1 for w in weeks}
    assert not calib._lost(real, back, "KC", 1, weeks)               # two games, then his job again
    late = {("KC", w): 1 if w < 17 else 2 for w in weeks}
    assert not calib._lost(real, late, "KC", 1, weeks)               # resting in the last weeks


def test_growth_recovers_a_known_chance_and_step():
    dice = np.random.default_rng(1)
    rows = []
    for i in range(6000):
        rookie = i % 3 == 0
        before = float(np.exp(dice.normal(1.5, 0.6)))
        step = 0.5 if rookie and dice.random() < 0.3 else 0.0
        rows.append((("RB", "WR", "TE")[i % 3 - 1], rookie, before, before * float(np.exp(dice.normal(0, 0.4) + step))))
    fit = calib.growth_fit(rows)
    assert fit["chance"] == pytest.approx(0.3, abs=0.1) and np.log(fit["gain"]) == pytest.approx(0.5, abs=0.12)


def test_second_half_change_is_measured_for_rookies_and_veterans_alike():
    data = fakes.fake_season()
    rookie = next(pid for pid, p in data.players.items() if p.position == "WR")
    out = sim_metrics.second_half(data, {rookie})
    assert set(out) == {"rookie 2nd-half ppg change", "veteran 2nd-half ppg change"}
    assert not np.isnan(out["veteran 2nd-half ppg change"])
