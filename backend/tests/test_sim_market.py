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
    ranks = sim_market.expert_ranks(belief, belief.players, 8, np.random.default_rng(1), HALF, 10)
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


def _position_ranks(board: dict, belief: sim_world.World, position: str) -> dict[int, int]:
    ranked = sorted((pick, pid) for pid, (pick, _) in board.items() if belief.players[pid].position == position)
    return {pid: rank for rank, (_, pid) in enumerate(ranked, start=1)}


def test_a_superflex_league_drafts_quarterbacks_early(belief):
    one_qb, superflex = sim_market.adp(belief, HALF), sim_market.adp(belief, HALF, two_qb=True)

    def early(board: dict) -> int:
        return sum(1 for pid, (pick, _) in board.items() if pick <= 24 and belief.players[pid].position == "QB")

    assert early(superflex) >= 4 > early(one_qb)


def test_standard_scoring_moves_a_pass_catching_back_down(belief):
    back = fakes.player_id("RB", 12)
    p = belief.players[back]
    catcher = replace(belief, players=MappingProxyType({**belief.players, back: replace(p, targets=p.targets * 2.5,
                                                                                         carries=p.carries * 0.5)}))
    ppr = _position_ranks(sim_market.adp(catcher, scoring_for("ppr")), catcher, "RB")
    std = _position_ranks(sim_market.adp(catcher, scoring_for("std")), catcher, "RB")
    assert std[back] > ppr[back]


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


def test_buzz_is_never_about_a_player_who_is_out_that_week(belief):
    qbs = sorted(pid for pid, p in belief.players.items() if p.position == "QB" and p.starts >= 0.5)
    on_ir, tagged = qbs[0], qbs[1]
    hurt = replace(belief, players=MappingProxyType({**belief.players, on_ir: replace(belief.players[on_ir], out_weeks=4)}))
    for seed in range(40):
        truth = sim_talent.truth(belief, seed, 1, 17)
        benched = [sim_world.RoleChange(week, pid, kind="bench") for week in range(1, 18) for pid in (on_ir, tagged)]
        for week in range(1, 18):
            notes = sim_market.buzz((*truth.changes, *benched), hurt, week, np.random.default_rng([seed, week]), 20.0,
                                    out={tagged})
            assert not {on_ir, tagged} & {pid for pid, _ in notes}

def test_the_market_and_the_experts_price_a_starter_on_ir_alike(belief, monkeypatch):
    """A jump-in draft: with the lead back on IR, his backup's ADP moves up with his expert rank (the experts and
    the market read the same values: the injured lead misses his first games, the backup takes his work)."""
    monkeypatch.setattr(sim_market, "EXPERT_NOISE", 1e-9)
    team = belief.players[fakes.player_id("RB", 1)].team
    lead, backup = sorted((p for p in belief.roster(team) if p.position == "RB"), key=lambda p: -p.carries)[:2]
    hurt = replace(belief, players=MappingProxyType({**belief.players, lead.player_id: replace(lead, out_weeks=4)}))
    games_left = 9
    healthy_board = _position_ranks(sim_market.adp(belief, HALF, games_left=games_left), belief, "RB")
    board = _position_ranks(sim_market.adp(hurt, HALF, games_left=games_left), hurt, "RB")
    experts = sim_market.expert_ranks(hurt, hurt.players, 10, np.random.default_rng(1), HALF, games_left)

    assert board[backup.player_id] < healthy_board[backup.player_id]
    assert healthy_board[lead.player_id] < board[lead.player_id] < len(board)       # discounted, still drafted
    assert {pid: experts[(pid, 10)] for pid in board} == board


def test_a_preseason_market_leans_on_last_seasons_record(data, belief):
    record = sim_market.track_record(data)
    games = Counter(pid for pid, _ in data.actual)
    assert record and all(games[pid] >= sim_market.RECORD_MIN_GAMES for pid in record)
    plain = sim_market.adp(belief, HALF)
    wr = _position_ranks(plain, belief, "WR")
    middling = next(pid for pid, rank in wr.items() if rank == 20)
    hot = sim_market.adp(belief, HALF, record={**record, middling: 40.0})
    assert hot[middling][0] < plain[middling][0]
