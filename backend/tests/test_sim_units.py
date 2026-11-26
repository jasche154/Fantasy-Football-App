"""Small hand-built checks of simulator helpers the season-level tests only reach indirectly (review of #66)."""
from types import SimpleNamespace

import numpy as np
import pytest

from services import sim_centre, sim_draw, sim_game, sim_lines, sim_metrics_games, sim_season, sim_talent, sim_world
from tests import replay_fakes as fakes

WEEK = 9


@pytest.fixture(scope="module")
def belief():
    return sim_world.estimate(fakes.fake_season(), WEEK)


def test_corr_and_fifths():
    assert sim_metrics_games.corr(np.array([1.0, 2, 3]), np.array([2.0, 4, 6])) == pytest.approx(1.0)
    assert np.isnan(sim_metrics_games.corr(np.array([1.0, 1, 1]), np.array([1.0, 2, 3])))      # a constant side
    assert np.isnan(sim_metrics_games.corr(np.array([1.0, 2]), np.array([1.0, 2])))           # too few pairs
    line = np.arange(1.0, 11.0)                                  # lopsided fifth: 9, 10; closest: 1, 2
    margin = np.array([25.0, 3, 0, 0, 0, 0, 0, 0, 30, -1])
    assert sim_metrics_games.fifths(line, margin) == (0.5, 0.5)
    assert sim_metrics_games.fifths(line, margin, blowout=26) == (0.5, 0.0)


def _game(week, home, away, spread, total):
    return SimpleNamespace(week=week, home_team=home, away_team=away, spread_line=spread, total_line=total)


def test_games_reads_the_line_and_the_scores():
    games = [_game(1, f"H{i}", f"A{i}", spread, 44.0) for i, spread in enumerate((3.0, -3.0, 7.0, 0.0, 10.0, 1.0))]
    scores = {("H0", 1): 24, ("A0", 1): 20, ("H1", 1): 10, ("A1", 1): 31, ("H2", 1): 17, ("A2", 1): 17,
              ("H3", 1): 0, ("A3", 1): 3, ("H4", 1): 35, ("A4", 1): 7, ("H5", 1): 21, ("A5", 1): 14}
    data = SimpleNamespace(schedule=SimpleNamespace(games=games), scores=scores)
    out = sim_metrics_games.games(data, [1])
    assert out["tie %"] == pytest.approx(100 / 6)
    assert out["home win %"] == pytest.approx(100 * (3 + 0.5) / 6)              # 3 home wins and a tie
    assert out["team score 0 %"] == pytest.approx(100 / 12)
    assert out["21+ margin %"] == pytest.approx(100 * 2 / 6)                    # -21 and +28
    assert out["line r margin"] > 0
    assert sim_metrics_games.games(SimpleNamespace(schedule=SimpleNamespace(games=games[:4]), scores=scores), [1]) == {}


def test_expert_churn_counts_healthy_top_players_week_to_week():
    players = {pid: SimpleNamespace(position="QB") for pid in (1, 2, 3)}
    ranks = {(1, 1): 1, (2, 1): 2, (3, 1): 3, (1, 2): 2, (2, 2): 1, (3, 2): 3}
    data = SimpleNamespace(players=players, expert_ranks=ranks, injuries={}, projected={})
    assert sim_metrics_games.experts(data, [1, 2])["expert churn QB"] == pytest.approx(2 / 3)
    hurt = SimpleNamespace(players=players, expert_ranks=ranks, injuries={(1, 2): fakes.Injury("Q")}, projected={})
    assert sim_metrics_games.experts(hurt, [1, 2])["expert churn QB"] == pytest.approx(1 / 2)   # a tagged one is left out


def _a_side(belief):
    home, away = next((g.home_team, g.away_team) for g in fakes.fake_season().schedule.games if g.week == WEEK)
    return sim_game._side_of(belief, home, away, set()), sim_game._side_of(belief, away, home, set())


def test_factor_hits_its_target_and_re_solves_past_the_td_rate_cap(belief):
    side, other = _a_side(belief)
    against = sim_game._side_means(other)
    for target in (12.0, 27.0):
        f = sim_centre.factor(side, against, target)
        got = sim_game._mean_points(sim_game._side_means(sim_centre.scaled(side, f, sim_lines.yards_power())), against)
        assert got == pytest.approx(target, abs=0.3)
    high = 70.0                                           # far past MAX_TD_RATE: the re-solve aims past the cap
    f = sim_centre.factor(side, against, high)
    assert max(side.rec_td.max(), side.rush_td.max()) * f > sim_game.MAX_TD_RATE
    got = sim_game._mean_points(sim_game._side_means(sim_centre.scaled(side, f, sim_lines.yards_power())), against)
    first = sim_centre._solve(float(sim_game._side_means(side)["rec_td"].sum() + sim_game._side_means(side)["rush_td"].sum()),
                              float(sim_game._side_means(side)["rec_yds"].sum() + sim_game._side_means(side)["rush_yds"].sum()),
                              0.0, side.fg_scale, sim_lines.yards_power(), high)
    assert f >= first and got > 40


def test_team_factor_moves_a_teams_mean_by_the_shift(belief):
    team = sorted(belief.teams)[0]
    side = sim_game._side_of(belief, team, None, set())
    before = sim_game._mean_points(sim_game._side_means(side), None)
    f = sim_centre.team_factor(belief, team, set(), 4.0)
    after = sim_game._mean_points(sim_game._side_means(sim_centre.scaled(side, f, sim_lines.yards_power())), None)
    assert after - before == pytest.approx(4.0, abs=0.3)


def test_thinned_keeps_each_kick_with_the_share():
    counts = {"made": np.array([2, 3, 1]), "missed": np.array([0, 1, 1]), "pat_made": 4, "pat_att": 5}
    rng = np.random.default_rng(0)
    whole = sim_draw._thinned(counts, 1.0, rng)
    assert (whole["made"] == counts["made"]).all() and whole["pat_made"] == 4 and whole["pat_att"] == 5
    none = sim_draw._thinned(counts, 0.0, rng)
    assert none["made"].sum() == none["missed"].sum() == none["pat_made"] == none["pat_att"] == 0
    for _ in range(50):
        half = sim_draw._thinned(counts, 0.5, rng)
        assert (half["made"] <= counts["made"]).all() and half["pat_made"] <= half["pat_att"] <= 5


def test_shift_centres_on_the_line_and_with_lines_stores_it():
    line = sim_lines.Line(margin=4.0, total=44.0, belief=(23.0, 20.0))
    hidden = {"HOM": (1.0, -0.5), "AWY": (0.0, 2.0)}
    shift = sim_season._shift(line, "HOM", "AWY", hidden)
    half = sim_talent.edge_scale() * 4.0 / 2
    assert shift == pytest.approx({"HOM": 22 + half - 23 + 1.0 + 2.0, "AWY": 22 - half - 20 + 0.0 - 0.5})
    assert sim_season._shift(None, "HOM", "AWY", hidden) is None
    assert sim_season._shift(sim_lines.Line(1.0, 40.0), "HOM", "AWY", hidden) is None      # no belief points
    schedule = fakes.fake_season().schedule
    game = next(g for g in schedule.games if g.week == WEEK)
    stored = sim_season._with_lines(schedule, {(game.home_team, WEEK): line})
    mine = next(g for g in stored.games if g.week == WEEK and g.home_team == game.home_team)
    assert (mine.spread_line, mine.total_line) == (4.0, 44.0)
    assert sum(g.spread_line == 4.0 for g in stored.games) == 1


def test_an_overtime_catch_is_never_the_hurt_starters():
    """Review item 12: with the starter gone (qb_share < 1), only regulation passing is split between the QBs."""
    side = SimpleNamespace(qb=1, backup=2, qb_share=0.7)
    regulation = SimpleNamespace(catches=np.array([5, 4, 0]), rec_yds=np.array([60, 40, 0]), rec_td=np.array([0, 0, 0]),
                                 ints=1, sack_fumbles=0)
    after = SimpleNamespace(**{**vars(regulation), "catches": np.array([6, 4, 0]), "rec_yds": np.array([72, 40, 0]),
                               "rec_td": np.array([1, 0, 0])})
    for seed in range(40):
        lines = sim_draw._passers(side, after, np.random.default_rng(seed), regulation)
        assert lines[1]["pass_td"] == 0 and lines[2]["pass_td"] == 1
        assert lines[1]["completions"] + lines[2]["completions"] == 10
        assert lines[1]["pass_yds"] + lines[2]["pass_yds"] == 112
