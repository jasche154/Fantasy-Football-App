"""sim_talent: the hidden truth differs from the belief, more for the less known, with dated role changes."""
import math
from dataclasses import replace
from types import MappingProxyType

import numpy as np
import pytest

from services import sim_talent, sim_world
from tests import replay_fakes as fakes


@pytest.fixture(scope="module")
def belief():
    return sim_world.estimate(fakes.fake_season(), 8)


def test_truth_is_deterministic_per_seed(belief):
    assert sim_talent.truth(belief, 1, 8, 17) == sim_talent.truth(belief, 1, 8, 17)
    assert sim_talent.truth(belief, 1, 8, 17) != sim_talent.truth(belief, 2, 8, 17)


def test_changes_are_dated_inside_the_season_and_name_real_players(belief):
    changes = [c for seed in range(20) for c in sim_talent.truth(belief, seed, 8, 17).changes]
    assert changes and all(9 <= c.week <= 17 - sim_talent.CHANGE_LAST_WEEKS for c in changes)
    assert all(c.player_id in belief.players for c in changes)
    assert {c.kind for c in changes} == {"breakout", "bust", "bench"}
    benched = [c for c in changes if c.kind == "bench"]
    assert all(belief.players[c.player_id].position == "QB" for c in benched)


def test_less_known_players_move_further_from_their_reputation(belief):
    known = {pid: replace(p, games=17.0) for pid, p in belief.players.items()}
    unknown = {pid: replace(p, games=0.0) for pid, p in belief.players.items()}

    def spread(players):
        world = replace(belief, players=MappingProxyType(players))
        logs = [math.log(t.targets / players[pid].targets)
                for seed in range(5) for pid, t in sim_talent.truth(world, seed, 8, 17).players.items()
                if players[pid].targets > 0.05]
        return float(np.std(logs))
    assert spread(unknown) > 1.5 * spread(known)


def test_the_truth_is_centred_on_the_belief(belief):
    ratios = [t.targets / belief.players[pid].targets for seed in range(10)
              for pid, t in sim_talent.truth(belief, seed, 8, 17).players.items() if belief.players[pid].targets > 0.05]
    assert np.mean(ratios) == pytest.approx(1.0, abs=0.03)
