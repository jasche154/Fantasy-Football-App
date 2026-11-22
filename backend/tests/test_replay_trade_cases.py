"""Replay League: the labeled trade set (tests/data/replay_trade_cases.json) on the committed season slices.

Each case is put to a CPU in three drafted leagues (seeds) under every persona: a must_accept deal is
accepted and a must_decline deal declined every time; each even deal only has to be judged without error (its
accept rate is printed, run with -s to see it), but pooled the even deals are accepted EVEN_RATE of the time. `python -m services.replay_trade_eval` prints the same on the
real database with every score.
"""
import pytest

from services import replay_trade_eval as te, replay_values as rv
from tests import replay_fakes as fakes

CASES = te.load_cases()
EVEN_RATE = (0.10, 0.70)        # wide: even deals should split, not all go one way


def test_the_set_is_balanced_and_well_formed():
    labels = [case["label"] for case in CASES]
    assert len({case["id"] for case in CASES}) == len(CASES)
    assert set(labels) == set(te.LABELS)
    assert min(labels.count(label) for label in te.LABELS) >= 12
    for case in CASES:
        assert 1 <= case["week"] <= 11 and case["scoring"] == "half"
        assert 1 <= len(case["cpu_gives"]) <= 3 and 1 <= len(case["cpu_gets"]) <= 3
        data = te.load_slice(case["season"])
        assert all(te.find(data, name) for name in (*case["cpu_gives"], *case["cpu_gets"]))


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_cpu_answers_as_labeled(case):
    data = te.load_slice(case["season"])
    results = [r for seed in te.SEEDS for r in te.judge(data, case, seed)]
    if case["label"] == "even":
        print(f"{case['id']}: accepted {sum(r.accepted for r in results)}/{len(results)}")
        return
    wrong = [f"seed {r.seed} {r.persona}: {'accepted' if r.accepted else 'declined'} score {r.score} {r.tally} "
             f"{' '.join(r.reasons)}" for r in results if not te.right(r)]
    assert not wrong, f"{case['id']} ({case['note']}):\n" + "\n".join(wrong)


def test_a_slice_rebuilds_the_same_values():
    """slice_json -> from_json keeps what the trade chart reads, to the rounding of a point."""
    data = fakes.fake_season()
    copy = te.from_json(te.slice_json(data))
    league = rv.league_settings(fakes.fake_settings())
    before, after = (rv.knowledge(d, league, 6, 16) for d in (data, copy))
    top = [v for v in before.values[:60]]
    assert all(abs(after.by_id[v.player_id].chart - v.chart) <= 5 for v in top)


def test_even_deals_split_between_accept_and_decline():
    results = [r for case in CASES if case["label"] == "even"
               for seed in te.SEEDS for r in te.judge(te.load_slice(case["season"]), case, seed)]

    rate = sum(r.accepted for r in results) / len(results)
    assert EVEN_RATE[0] <= rate <= EVEN_RATE[1], f"even deals accepted {rate:.0%}"
