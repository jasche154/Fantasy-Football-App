"""lineup_gain's best-lineup solve (`_greedy`, `week_lineup`): the same starters as my_team.best_lineup's scan
of the pool per slot, in its slot order. The CPU trade seeds' offers with the old solve: test_replay_trade_ai."""
import random
from types import SimpleNamespace

import pytest

from services import lineup_gain, my_team as mt, replay_values as rv
from tests import replay_fakes as fakes


def reference_greedy(modeled, points, counts):
    """The lineup as my_team.best_lineup picks it (its scan of the pool per slot), in its slot order."""
    own, flex, superflex = counts
    starters = {**dict(zip(mt.MODELED, own)), "FLEX": flex, "SUPERFLEX": superflex}
    pool = [SimpleNamespace(player_id=v.player_id, position=v.position, projection=points[v.player_id])
            for v in modeled]
    best = mt.best_lineup(pool, SimpleNamespace(starters=starters, superflex=False))
    return [pid for ids in best.values() for pid in ids]


@pytest.mark.parametrize("counts", [((1, 2, 2, 1), 1, 0), ((1, 2, 2, 1), 2, 1), ((2, 1, 3, 1), 0, 1), ((1, 2, 3, 1), 0, 0)])
def test_the_lineup_solver_picks_what_the_slot_scan_picks_in_its_order(counts):
    rng = random.Random(7)
    for _ in range(300):
        size = rng.randint(0, 18)
        modeled = sorted((SimpleNamespace(player_id=rng.randint(1, 60), position=rng.choice(("QB", "RB", "WR", "TE")))
                          for _ in range(size)), key=lambda v: v.player_id)
        modeled = list({v.player_id: v for v in modeled}.values())
        points = {v.player_id: rng.choice((0.0, -0.0, 5.0, 10.0, round(rng.uniform(-2, 25), 1))) for v in modeled}
        assert lineup_gain._greedy(modeled, points, counts) == reference_greedy(modeled, points, counts)


def test_slot_counts_read_the_leagues_lineup():
    league = rv.league_settings(fakes.fake_settings(starters={**fakes.fake_settings().starters, "SUPERFLEX": 1}))
    counts = lineup_gain._slot_counts(league)
    assert counts == (tuple(league.starters.get(p, 0) for p in ("QB", "RB", "WR", "TE")),
                      league.starters.get("FLEX", 0), 1)

