"""The review round's L4: the calendar's de-seasoning, the join scale's wiring, the small fits (sim_calendar.fit,
sim_closing_fit.line_script / td_yards / check) and the new sim_eval rows, each on a tiny fixture."""
from types import SimpleNamespace

import numpy as np
import pytest

from services import (sim_calendar, sim_closing_fit, sim_constants, sim_eval, sim_metrics_games, sim_metrics_join,
                      sim_season, sim_world)
from services.replay_box import BoxLine
from services.schedule_strength import Game
from tests import replay_fakes as fakes


class _Rows:
    """A stand-in session: each execute() answers with the rows of the first key its SQL contains."""

    def __init__(self, answers: dict[str, list[tuple]]):
        self.answers = answers

    def execute(self, sql):
        return next(rows for key, rows in self.answers.items() if key in str(sql))


def _game(week: int, home: str, away: str, spread: float, total: float = 44.0) -> Game:
    return Game(week=week, home_team=home, away_team=away, home_score=None, away_score=None, spread_line=spread,
                total_line=total)


def test_the_calendar_is_measured_against_each_seasons_mean():
    games = [(2021, w, 2) for w in range(1, 19)]
    stats = [(2021, w, 80.0 if w <= 4 else 60.0, 50.0, 600.0 if w <= 4 else 480.0) for w in range(1, 19)]
    fit = sim_calendar.fit(_Rows({"team_game_stats": games, "player_stats_weekly": stats}), [2021])
    first, last = fit["buckets"][0], fit["buckets"][-1]
    mean = (4 * 40.0 + 14 * 30.0) / 18                     # targets a team-game over the season
    assert first["targets"] == pytest.approx(40.0 / mean, abs=1e-3) and last["targets"] == pytest.approx(30.0 / mean, abs=1e-3)
    assert first["carries"] == pytest.approx(1.0) and first["ypt"] == pytest.approx(7.5 / np.mean([7.5] * 4 + [8.0] * 14), abs=1e-3)


def test_the_belief_learns_team_volume_without_the_calendar(monkeypatch):
    data = fakes.fake_season()
    plain = sim_world.estimate(data, 9)
    monkeypatch.setattr(sim_calendar, "factors", lambda week: (2.0, 1.0, 1.0))   # every week twice the usual passing
    seasoned = sim_world.estimate(data, 9)
    team = sorted(plain.teams)[0]
    assert seasoned.teams[team].targets < 0.8 * plain.teams[team].targets
    assert seasoned.teams[team].carries == pytest.approx(plain.teams[team].carries, rel=0.02)


def test_the_first_simulated_week_after_real_ones_takes_the_join_scale(monkeypatch):
    data, start = fakes.fake_season(), 15
    monkeypatch.setitem(sim_constants._overrides, "market", {"projection_scale": {}, "join_fade": 0.0,
                                                             "join_scale": {"QB": 1.5, "RB": 1.5, "WR": 1.5, "TE": 1.5}})
    sim_constants.params.cache_clear()
    joined = sim_season.simulate(data, 3, start)
    monkeypatch.setitem(sim_constants._overrides, "market", {"projection_scale": {}, "join_fade": 0.0, "join_scale": {}})
    sim_constants.params.cache_clear()
    plain = sim_season.simulate(data, 3, start)
    monkeypatch.undo()
    sim_constants.params.cache_clear()

    def ratio(week: int) -> float:
        keys = [k for k in plain.projected if k[1] == week and k in joined.projected and plain.projected[k] > 5
                and joined.players[k[0]].position in ("QB", "RB", "WR", "TE")]
        return float(np.median([joined.projected[k] / plain.projected[k] for k in keys]))
    assert ratio(start) == pytest.approx(1.5, abs=0.01) and ratio(start + 1) == pytest.approx(1.0, abs=0.01)


def test_the_line_script_is_the_within_team_season_slope_on_the_spread():
    lines = [(2021, w, "AAA", "BBB", 6.0 if w % 2 else -6.0) for w in range(1, 9)]
    stats = []
    for season, week, home, away, spread in lines:          # AAA runs 6% more when favoured by 6
        favoured = spread > 0
        stats.append((season, week, away, 30.0, 28.0 * (1.06 if favoured else 1.0)))    # AAA's game (opponent BBB)
        stats.append((season, week, home, 30.0, 28.0))
    got = sim_closing_fit.line_script(_Rows({"nfl_games": lines, "player_stats_weekly": stats}), [2021])
    # pooled over both teams (BBB's volume never moves): half of AAA's own slope, log(1.06) over 12 points
    assert got["carries"] == pytest.approx(np.log(1.06) / 24, abs=1e-4) and got["targets"] == pytest.approx(0.0)


def test_window_td_yards_and_the_leave_season_out_check_read_the_plays():
    plays = [[4, 300, "AAA", "pass_td", 20], [4, 300, "AAA", "xp", 0], [4, 100, "BBB", "rush_td", 4],
             [4, 100, "BBB", "xp", 0], [2, 1800, "AAA", "pass_td", 60], [2, 1800, "AAA", "xp", 0]]
    games = [{"season": s, "week": 1, "home": "AAA", "away": "BBB", "home_score": 14, "away_score": 7,
              "plays": plays} for s in (2020, 2021)]
    assert sim_closing_fit.td_yards(games, 600) == {"rec": 20.0, "rush": 4.0}     # the 60-yarder was before the cut
    checked = sim_closing_fit.check(games, 600, repeats=3)
    assert checked["real"]["7"] == 1.0 and checked["played forward"]["7"] == 1.0  # 7-0 at the cut, +7 / +7 after


def test_close_games_and_the_line_script_row():
    margins = np.array([3, -3, 7, 1, 10, 0, 21, -5])
    rows = sim_metrics_games.close_games(margins)
    assert rows == {"margin 1-3 %": 37.5, "margin 3 %": 25.0, "margin 7 %": 12.5, "margin 4-8 %": 25.0}
    games = [_game(w, "AAA", "BBB", 6.0 if w % 2 else -6.0) for w in range(1, 9)]
    box = {(1, w): BoxLine(targets=30, carries=round(28 * (1.06 if w % 2 else 1.0))) for w in range(1, 9)}
    data = SimpleNamespace(schedule=SimpleNamespace(games=games), box=box,
                           team_by_week={1: tuple((w, "AAA") for w in range(1, 9))})
    got = sim_metrics_games.line_script(data, range(1, 9))
    assert got["carries per line point %"] == pytest.approx(100 * np.log(30 / 28) / 12, abs=0.05)
    assert got["targets per line point %"] == pytest.approx(0.0)


def test_the_line_follow_row_is_the_within_player_slope_on_the_implied_total():
    games = [_game(w, "AAA", "BBB", 0.0, 40.0 if w % 2 else 50.0) for w in range(1, 7)]
    projected = {(1, w): 20.0 * ((20.0 if w % 2 else 25.0) / 20.0) ** 0.5 for w in range(1, 7)}
    data = SimpleNamespace(schedule=SimpleNamespace(games=games), projected=projected, injuries={},
                           players={1: SimpleNamespace(position="QB")}, team_at=lambda pid, week: "AAA")
    assert sim_metrics_join.line_follow(data, range(1, 7))["proj follows line QB"] == pytest.approx(0.5)


def test_percentile_rows_pool_every_season_and_seed():
    rows = [{"x": 1.0, "leader p95": [float(v)]} for v in range(1, 21)]
    out = sim_eval._percentiles(sim_eval._mean([sim_eval._mean(rows[:10]), sim_eval._mean(rows[10:])]))
    assert out["x"] == 1.0 and out["leader p95"] == pytest.approx(np.quantile(range(1, 21), 0.95))
