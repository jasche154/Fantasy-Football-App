"""sim_next_year_calib: the whole-season fits (efficiency scale, rookie growth) on synthetic data."""

import numpy as np
import pytest

from services import sim_metrics, sim_next_year_calib as calib
from tests import replay_fakes as fakes


def test_the_scale_with_the_least_error_wins_per_position_over_the_given_seasons():
    by_scale = {0.0: {2021: {"QB": (5.0, 10), "WR": (1.0, 10)}, 2022: {"QB": (5.0, 10), "WR": (9.0, 10)}},
                1.0: {2021: {"QB": (1.0, 10), "WR": (3.0, 10)}, 2022: {"QB": (1.0, 10), "WR": (3.0, 10)}}}
    assert calib.best_scale(by_scale, [2021, 2022])["QB"] == 1.0
    assert calib.best_scale(by_scale, [2021, 2022])["WR"] == 1.0
    assert calib.best_scale(by_scale, [2021])["WR"] == 0.0          # leaving 2022 out changes the answer


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
