"""sim_eval / sim_metrics: the realism gate measures real and simulated seasons the same way (no database)."""
import math
from dataclasses import replace
from types import MappingProxyType

import pytest

from services import sim_eval, sim_metrics, sim_world
from tests import replay_fakes as fakes


@pytest.fixture(scope="module")
def data():
    return fakes.fake_season()


def test_spearman():
    assert sim_metrics.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert sim_metrics.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert math.isnan(sim_metrics.spearman([1], [2]))


def test_tiers_follow_adp_position_ranks(data):
    tiers = sim_metrics.tiers(data)
    ranks = sim_metrics.adp_ranks(data)
    assert all(tier == f"{data.players[pid].position}{(ranks[pid] - 1) // 12 + 1}" for pid, tier in tiers.items())
    assert {tier for tier in tiers.values()} >= {"QB1", "QB2", "RB1", "RB3", "WR3", "TE2"}


def test_weekly_and_season_metrics_cover_the_report(data):
    weekly = sim_metrics.weekly(data, range(10, 18))
    for key in ("RB1 q50", "25+ share %", "proj RMSE all", "proj rank r all", "sitting/wk RB", "team pts sd", "K mean"):
        assert key in weekly and not math.isnan(weekly[key])
    season = sim_metrics.season(data)
    assert set(season) >= {"ADP vs finish r WR", "breakouts", "busts", "games missed / top-150"}
    assert season["ADP vs finish r RB"] > 0.5          # the fake season mostly plays to its ADP


def test_the_mid_season_base_knows_nothing_from_the_cut_on(data):
    base = sim_eval.cut(data, 12)
    assert max(w for _, w in base.actual) == 11 and base.known_before is None
    assert base.schedule == data.schedule and base.last_week == data.last_week


def test_real_moves_are_read_from_the_next_seasons_games(data):
    belief = sim_world.estimate(data, data.last_week + 1)
    mover, gone, stays = fakes.player_id("WR", 3), fakes.player_id("RB", 60), fakes.player_id("QB", 1)
    new_team = next(t for t in belief.teams if t != belief.players[mover].team)
    games = {**data.team_by_week, mover: ((1, new_team), (2, new_team))}
    games.pop(gone)
    players = {pid: p for pid, p in data.players.items() if pid != gone}
    later = replace(data, team_by_week=MappingProxyType(games), players=MappingProxyType(players))

    moves = sim_eval.real_moves(belief, later)

    assert moves[mover] == new_team and moves[gone] is None and stays not in moves


def test_season_ranges_parse():
    assert sim_eval._seasons("2021-2023") == [2021, 2022, 2023]
    assert sim_eval._seasons("2019,2024") == [2019, 2024]


def test_rookie_and_market_metrics(data):
    rookies = {fakes.player_id("WR", rank) for rank in range(1, 30)} | {fakes.player_id("QB", 1)}
    found = sim_metrics.rookies(data, rookies)
    assert found["rookie top-24 finishes"] > 0 and found["rookie QB wk1 starters"] in (0.0, 1.0)
    assert sim_metrics.adp_agreement(data, data)["ADP vs real ADP r (top-150)"] == pytest.approx(1.0)


def test_the_leaderboard_and_big_games_need_no_adp(data):
    board = sim_metrics.leaderboard(data)
    for position in sim_metrics.SKILL:
        ppg = [board[f"{position} top-{n} ppg"] for n in sim_metrics.LEADERS]
        assert ppg == sorted(ppg, reverse=True) and ppg[-1] > 0
    no_adp = replace(data, players=MappingProxyType({pid: replace(p, adp=None) for pid, p in data.players.items()}))
    assert sim_metrics.leaderboard(no_adp) == board
    weekly = sim_metrics.weekly(data, range(1, 18))
    assert weekly["25+ games"] >= weekly["30+ games"] >= weekly["35+ games"]
    assert sim_metrics.leaderboard(data, range(10, 18)) != board
