"""sim_setup._per_data: the parts of a league's season every seed shares, computed once per season object."""
from dataclasses import replace

from services import replay_pool, sim_setup
from tests import replay_fakes as fakes


def test_computed_once_per_season_object_and_emptied_with_the_pool_cache():
    data, calls = fakes.fake_season(), []

    def compute():
        calls.append(1)
        return len(calls)

    assert sim_setup._per_data(data, "x", compute) == 1 == sim_setup._per_data(data, "x", compute)
    assert sim_setup._per_data(replace(data), "x", compute) == 2              # new data: a new object
    assert sim_setup._per_data(data, "y", compute) == 3                      # another part
    replay_pool.clear_cache()
    assert sim_setup._per_data(data, "x", compute) == 4
