"""Replay League CPU personas (services/replay_personas.py): assignment, private values, rates, tendencies."""
from dataclasses import replace

import pytest

from services import replay_personas as rp
from tests import replay_fakes as fakes

CPUS = tuple(range(2, 13))


def player(**fields):
    return replace(fakes.fake_knowledge(1).values[0], **{"chart": 500.0, "pre_chart": 500.0, "star": 0.0,
                                                        "form_chart": 0.0, **fields})


def test_assignment_is_seeded_and_always_has_a_shark_and_a_panicky_manager():
    for seed in range(20):
        picked = rp.assign(seed, CPUS)

        assert picked == rp.assign(seed, CPUS) and list(picked) == list(CPUS)
        assert set(picked.values()) <= set(rp.PERSONAS) and {"shark", "panicky"} <= set(picked.values())
    assert len({tuple(rp.assign(seed, CPUS).values()) for seed in range(20)}) > 1
    assert set(rp.assign(0, [3]).values()) <= set(rp.PERSONAS) and rp.assign(0, []) == {}
    assert sorted(rp.assign(4, [7, 9]).values()) == ["panicky", "shark"]


@pytest.mark.parametrize("persona, fields, expected", [
    ("balanced", {"form_chart": -200.0}, 500.0),
    ("shark", {"form_chart": 200.0}, 500.0),
    ("panicky", {"form_chart": -50.0}, 460.0),           # 0.8 x the slump
    ("panicky", {"form_chart": 80.0}, 500.0),            # a hot streak doesn't excite him
    ("panicky", {"form_chart": -300.0}, 425.0),          # clamped at -15%
    ("gambler", {"form_chart": 60.0}, 530.0),            # 0.5 x the streak
    ("gambler", {"form_chart": -60.0}, 500.0),
    ("steady", {"star": 1.0, "pre_chart": 600.0}, 535.0),     # 0.35 x star x (pre - now)
    ("steady", {"star": 0.0, "pre_chart": 900.0}, 500.0),     # only stars
])
def test_private_values_nudge_the_chart_within_fifteen_percent(persona, fields, expected):
    assert rp.private_value(player(**fields), persona) == pytest.approx(expected)


def test_emotional_managers_overreact_to_a_shock_and_calm_ones_keep_the_chart():
    lifted, dropped = player(shock_chart=20.0), player(shock_chart=-20.0)

    assert rp.hype_for("panicky") == rp.hype_for("gambler") == rp.HYPE > 1.0
    assert {rp.hype_for(p) for p in ("balanced", "shark", "steady", "unknown")} == {1.0}
    assert rp.private_value(lifted, "balanced") == rp.private_value(lifted, "balanced", 1.0) == 500.0
    assert rp.private_value(lifted, "balanced", rp.HYPE) == pytest.approx(500.0 + (rp.HYPE - 1) * 20)
    assert rp.private_value(dropped, "balanced", rp.HYPE) == pytest.approx(500.0 - (rp.HYPE - 1) * 20)
    assert rp.private_value(player(shock_chart=900.0), "balanced", rp.HYPE) == 500.0 + rp.HYPE_CAP   # a few points at most
    assert rp.private_value(player(), "gambler", rp.HYPE) == 500.0                                   # no shock, no hype


def test_rates_and_tendencies():
    assert rp.offer_rate("shark") == max(rp.offer_rate(p) for p in rp.PERSONAS) == 0.30
    assert rp.offer_rate("steady") == 0.05 and rp.offer_rate("unknown") == rp.offer_rate("balanced")
    assert all(0 < rp.counter_rate(p) <= 1 for p in rp.PERSONAS)
    assert rp.tendency("panicky", rp.TENDENCY_WEEKS - 1) is None
    assert rp.tendency("panicky", rp.TENDENCY_WEEKS) == "Sells after a bad week"
    assert rp.tendency("shark", 9) == "Hunts for buy-low stars"
    assert all(len(rp.tendency(p, 5)) < 30 for p in rp.PERSONAS)
