"""sim_market: projections, expert ranks, ADP and buzz read the belief (buzz alone hints the truth)."""
from collections import Counter
from dataclasses import replace
from types import MappingProxyType

import numpy as np
import pytest

from services import sim_market, sim_talent, sim_world
from services.scoring import scoring_for
from tests import replay_fakes as fakes

HALF = scoring_for("half")


@pytest.fixture(scope="module")
def data():
    return fakes.fake_season()


@pytest.fixture(scope="module")
def belief(data):
    return sim_world.estimate(data, 8)


def test_projections_cover_the_weeks_games_and_a_player_out_lifts_his_teammates(data, belief):
    healthy = sim_market.projections(belief, data.schedule, 8, set(), HALF)
    playing = {t for g in data.schedule.games if g.week == 8 for t in (g.home_team, g.away_team)}
    assert {belief.players[p].team for p in healthy} <= playing
    wr1 = fakes.player_id("WR", 1)
    if belief.players[wr1].team not in playing:
        pytest.skip("WR1 is on bye in week 8")
    out = sim_market.projections(belief, data.schedule, 8, {wr1}, HALF)
    assert wr1 not in out
    teammates = [p for p in out if belief.players[p].team == belief.players[wr1].team and belief.players[p].position == "WR"]
    assert teammates and all(out[p] >= healthy[p] for p in teammates)


def test_expert_ranks_run_1_to_n_at_each_position(belief):
    ranks = sim_market.expert_ranks(belief, belief.players, 8, np.random.default_rng(1), HALF, set(), 10)
    by_position = Counter(belief.players[pid].position for pid, _ in ranks)
    for position, count in by_position.items():
        assert sorted(r for (pid, _), r in ranks.items() if belief.players[pid].position == position) == list(range(1, count + 1))


def test_adp_drafts_the_believed_best_and_leaves_out_a_player_believed_out_for_the_season(belief):
    board = sim_market.adp(belief)
    positions = Counter(belief.players[p].position for p in board)
    assert positions["K"] and positions["DST"] and len(board) > 200
    assert all(stdev >= sim_market.MIN_ADP_STDEV for _, stdev in board.values())
    first = min(board, key=lambda p: board[p][0])
    gone = replace(belief, players=MappingProxyType({**belief.players, first: replace(belief.players[first], out_weeks=17)}))
    assert first not in sim_market.adp(gone)


def test_buzz_hints_most_changes_and_is_right_about_as_often_as_promised(belief):
    true = wrong = hinted = changed = 0
    for seed in range(40):
        truth = sim_talent.truth(belief, seed, 1, 17)
        rate = sim_market.expected_hints(len(belief.teams), 17, sim_talent.settings())
        for week in range(1, 18):
            notes = sim_market.buzz(truth.changes, belief, week, np.random.default_rng([seed, week]), rate)
            real = {c.player_id for c in truth.changes if c.week == week}
            changed += len(real)
            hinted += len(real & {p for p, _ in notes})
            true += sum(p in real for p, _ in notes)
            wrong += sum(p not in real for p, _ in notes)
    assert hinted / changed == pytest.approx(sim_market.HINT_RATE, abs=0.08)
    assert true / (true + wrong) == pytest.approx(sim_market.PRECISION, abs=0.1)
