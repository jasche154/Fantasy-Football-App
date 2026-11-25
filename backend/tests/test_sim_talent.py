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
    # who starts at QB is decided week by week (sim_qb_jobs), never pre-rolled here
    assert {c.kind for c in changes} == {"breakout", "bust"}


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


def test_a_whole_season_draws_the_talent_its_regressed_belief_cannot_know(belief):
    """From week 1 each rate gets its own offseason draw (wider for a player with no games); mid-season none."""
    receivers = sorted(pid for pid, p in belief.players.items() if p.position == "WR" and p.targets > 0.05)
    rookie_like = replace(belief, players=MappingProxyType(
        {**belief.players, **{pid: replace(belief.players[pid], games=0.0) for pid in receivers[::2]}}))

    truths = {week: [sim_talent.truth(rookie_like, seed, week, 17) for seed in range(12)] for week in (1, 8)}

    def td_spread(pids, from_week):
        logs = [math.log(truth.players[pid].rec_td / belief.players[pid].rec_td)
                for truth in truths[from_week] for pid in pids]
        return float(np.std(logs))

    veterans, rookies = receivers[1::2], receivers[::2]
    assert td_spread(veterans, 1) > td_spread(veterans, 8) * 1.5          # the offseason adds to the in-season spread
    assert td_spread(rookies, 1) > td_spread(veterans, 1)
    assert all(p.catch <= sim_talent.MAX_CATCH for p in truths[1][0].players.values())


def test_stars_never_bust_and_rumours_spare_them(belief):
    ranks = sim_talent.public_ranks(belief)
    stars = {pid for pid, rank in ranks.items() if rank <= sim_talent.STAR_RANK}
    busted = {c.player_id for seed in range(40) for c in sim_talent.truth(belief, seed, 1, 17).changes if c.kind == "bust"}
    assert busted and not busted & stars
    rumors = sim_talent.rumors(belief, ranks)
    assert rumors["bust"] and not set(rumors["bust"]) & stars
    assert rumors["bench"] == {}                         # job-in-danger rumours come from sim_qb_jobs only
    assert sim_talent.rumors(belief, ranks, {7: 0.3})["bench"] == {7: 0.3}


def test_the_starters_are_ranked_among_the_starters(belief):
    ranks = sim_talent.public_ranks(belief)
    starters = {max((p for p in belief.roster(t) if p.position == "QB"), key=lambda p: (p.starts, -p.player_id)).player_id
                for t in belief.teams if any(p.position == "QB" for p in belief.roster(t))}
    assert sorted(ranks[pid] for pid in starters) == list(range(1, len(starters) + 1))


def test_rookies_grow_after_mid_season_at_the_fitted_rate(belief):
    """Only the rookies, only after from_week, about `chance` of them, by `gain` (sim_next_year.json)."""
    fit = sim_talent.sim_next_year.stored()["rookie_growth"]
    rookies = sorted(pid for pid, p in belief.players.items() if p.position in ("RB", "WR", "TE"))[:40]
    steps = [c for seed in range(30) for c in sim_talent.truth(belief, seed, 1, 17, rookies).changes if c.kind == "growth"]
    assert {c.player_id for c in steps} <= set(rookies)
    assert len(steps) / (30 * len(rookies)) == pytest.approx(fit["chance"], abs=0.05)
    assert all(c.targets == c.carries == fit["gain"] and fit["split_week"] - 1 <= c.week <= fit["split_week"] + 2
               for c in steps)
    late = [c for seed in range(30) for c in sim_talent.growth(rookies, seed, fit["split_week"])]
    assert late and all(c.week > fit["split_week"] for c in late)          # a live season's real weeks stay real
    assert not [c for c in sim_talent.truth(belief, 1, 1, 17).changes if c.kind == "growth"]


def test_rookie_qbs_and_kickers_never_grow(belief):
    """Growth is fitted on rookie RBs, WRs and TEs only: a rookie QB or K keeps his role."""
    rookies = [pid for pid, p in belief.players.items() if p.position in ("QB", "K")]
    assert rookies
    assert not [c for seed in range(30) for c in sim_talent.truth(belief, seed, 1, 17, rookies).changes
                if c.kind == "growth"]
