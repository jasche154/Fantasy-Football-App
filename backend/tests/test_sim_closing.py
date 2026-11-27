"""sim_closing / sim_closing_fit: a game's closing window played from the score, and overtime's measured rates."""
from collections import Counter

import numpy as np
import pytest

from services import sim_calendar, sim_closing, sim_closing_fit, sim_closing_means, sim_draw, sim_game
from tests.test_sim_game import HALF, pid, world

NONE = dict.fromkeys(sim_closing_fit.EVENTS, 0)


def _events(**counts: int) -> dict:
    return {**NONE, **counts}


def _game(plays: list, home_score: int, away_score: int, season: int = 2020) -> dict:
    return {"season": season, "week": 1, "home": "AAA", "away": "BBB", "home_score": home_score,
            "away_score": away_score, "plays": plays}


def test_a_scoring_play_is_read_by_kind_and_team():
    base = {"touchdown": 0, "td_team": None, "pass_touchdown": 0, "rush_touchdown": 0, "field_goal_result": None,
            "kick_distance": None, "extra_point_result": None, "two_point_conv_result": None, "safety": 0,
            "posteam": "AAA", "defteam": "BBB"}
    kind = sim_closing_fit._kind
    assert kind({**base, "touchdown": 1, "td_team": "AAA", "pass_touchdown": 1}) == ("AAA", "pass_td")
    assert kind({**base, "touchdown": 1, "td_team": "BBB"}) == ("BBB", "ret_td")          # a pick six
    assert kind({**base, "field_goal_result": "made", "kick_distance": 47}) == ("AAA", "fg_40_49")
    assert kind({**base, "field_goal_result": "blocked", "kick_distance": 55}) == ("AAA", "miss_50")
    assert kind({**base, "safety": 1}) == ("BBB", "safety")
    assert kind(base) is None


def test_endings_are_filed_by_the_margin_at_the_cut_from_the_trailers_side():
    late = 300
    games = [_game([[2, 900, "AAA", "fg_0_39", 0], [4, late, "BBB", "rush_td", 2], [4, late, "BBB", "xp", 0]], 3, 7),
             _game([[1, 3000, "AAA", "pass_td", 20], [1, 3000, "AAA", "xp", 0], [5, 400, "BBB", "fg_0_39", 0]], 7, 3)]
    assert all(sim_closing_fit.consistent(g) for g in games)
    table = sim_closing_fit.endings(games, 600)
    (trail, lead), = table[3]                       # BBB trailed 0-3 and scored a TD and the extra point
    assert dict(zip(sim_closing_fit.EVENTS, trail))["rush_td"] == 1 and not any(lead)
    assert sum(table[7].values()) == 1              # the second game: no scoring in its window (OT is not the window)
    ot = sim_closing_fit.overtime(games)
    assert ot["games"] == 1 and ot["tie"] == 0.0 and ot["field_goal"] == 0.0   # BBB lost: the OT kick was not the winner's
    assert not sim_closing_fit.consistent(_game([[1, 3000, "AAA", "pass_td", 20]], 7, 0))


def test_a_trailer_scores_more_in_the_window_than_a_leader_and_close_games_tie_more():
    window = sim_closing_means.window_points
    assert window(-10, 11.0) > window(0, 11.0) > window(10, 11.0) > 0
    tie = sim_closing_means.tie_chance
    assert tie(0, 8.0) > tie(0, 11.0) > tie(7, 11.0) > tie(20, 11.0) > 0
    assert tie(3, 11.0) == tie(-3, 11.0)
    assert sim_closing_means.cut_sd(14.0) < sim_closing_means.cut_sd(36.0) < sim_closing_means.cut_sd(56.0)


def test_the_draw_before_the_cut_plus_window_and_overtime_average_the_wanted_points():
    pre_a, pre_b, end_a, end_b = sim_closing_means.regulation(27.0, 17.0)
    assert pre_a + end_a.window + end_a.overtime == pytest.approx(27.0, abs=0.01)
    assert pre_b + end_b.window + end_b.overtime == pytest.approx(17.0, abs=0.01)
    assert end_a.lead == pytest.approx(pre_a - pre_b) and end_a.share + end_b.share == pytest.approx(1.0)


def _setup(exits: dict | None = None):
    w = world()
    sides = [sim_game._side(w, "AAA", "BBB", set(), exits or {}), sim_game._side(w, "BBB", "AAA", set(), {})]
    means = [sim_game._side_means(s) for s in sides]
    rng = np.random.default_rng(4)
    offense = [sim_draw._offense(s, 33, 27, 1.0, rng) for s in sides]
    counts = {"made": np.zeros(3, dtype=int), "missed": np.zeros(3, dtype=int), "pat_made": 0, "pat_att": 0}
    return sides, means, offense, counts, rng


def test_the_trailers_ending_goes_to_the_trailing_side_and_to_real_players(monkeypatch):
    star = pid("AAA", "WR")
    sides, means, offense, counts, rng = _setup({star: 0.1})
    ending = (_events(pass_td=2, fg_40_49=1, xp=2), _events(ret_td=1, safety=1))
    monkeypatch.setattr(sim_closing, "ending", lambda margin, rng: ending)
    for _ in range(50):
        out = sim_closing.close(sides, offense, [(10, counts), (17, counts)], [10, 17], [0, 0], [0, 0],
                                np.array([33, 33]), np.array([27, 27]), rng, means, [20.0, 20.0])
        after, scored, points, returns, safeties, n_targets, _ = out
        assert points == [10 + 12 + 3 + 2, 17 + 6 + 2]                 # AAA trailed 10-17: the trailer's ending
        assert returns == [0, 1] and safeties == [0, 1]
        assert scored[0][1]["made"].tolist() == [0, 1, 0] and scored[0][1]["pat_att"] == 2
        gained = after[0].rec_td - offense[0].rec_td
        assert gained.sum() == 2 and n_targets[0] == 35
        assert after[0].attempts - offense[0].attempts == 2
        assert all(n == 0 for p, n in zip(sides[0].receivers, gained) if p == star)   # he left hurt


def test_drawn_games_average_the_projections_with_the_window_in():
    w, rng = world(), np.random.default_rng(8)
    target = {"AAA": 30.0, "BBB": 17.0}
    expected = sim_game.expected(w, "AAA", "BBB", set(), HALF, target=target)
    side = sim_game._side(w, "AAA", "BBB", set(), {})
    kicker, skilled = side.kicker, set(side.positions) - {side.kicker, side.dst}
    qb = pid("AAA", "QB")
    totals: Counter = Counter()
    games = 3000
    for _ in range(games):
        result = sim_game.play(w, "AAA", "BBB", set(), rng, HALF, target=target)
        totals["AAA"] += result.points["AAA"]
        totals["kicker"] += result.players[kicker].points
        totals["qb_td"] += result.players[qb].box.pass_td if qb in result.players else 0
        totals["skill"] += sum(g.points for p, g in result.players.items() if p in skilled)
    skill = sum(e.points for p, e in expected.items() if p in skilled)
    assert totals["AAA"] / games == pytest.approx(30.0, abs=0.35)
    assert totals["kicker"] / games == pytest.approx(expected[kicker].points, abs=0.25)
    assert totals["qb_td"] / games == pytest.approx(expected[qb].box.pass_td, abs=0.06)
    assert totals["skill"] / games == pytest.approx(skill, rel=0.03)


def test_late_season_games_run_more_and_throw_less_at_the_same_points():
    early_targets, early_carries, _ = sim_calendar.factors(2)
    late_targets, late_carries, late_ypt = sim_calendar.factors(16)
    assert late_targets < 1.0 < early_targets and early_carries < 1.0 < late_carries and late_ypt < 1.0
    w, target = world(), {"AAA": 24.0, "BBB": 20.0}
    early = sim_game.expected(w, "AAA", "BBB", set(), HALF, target=target, week=2)
    late = sim_game.expected(w, "AAA", "BBB", set(), HALF, target=target, week=16)
    rb, qb = pid("AAA", "RB"), pid("AAA", "QB")
    assert late[rb].box.carries > early[rb].box.carries and late[qb].box.pass_yds < early[qb].box.pass_yds


def test_a_favourite_runs_more_than_the_same_team_as_an_underdog():
    w, rb = world(), pid("AAA", "RB")
    favoured = sim_game.expected(w, "AAA", "BBB", set(), HALF, target={"AAA": 30.0, "BBB": 16.0})
    underdog = sim_game.expected(w, "AAA", "BBB", set(), HALF, target={"AAA": 16.0, "BBB": 30.0})
    ratio = favoured[rb].box.carries / underdog[rb].box.carries
    assert ratio == pytest.approx(np.exp(28 * sim_closing.line_script()["carries"]), rel=0.02)


def test_over_many_games_the_box_adds_up_to_the_points_and_no_try_comes_without_a_touchdown(monkeypatch):
    """M3: a real ending's try is filed with its touchdown, so a kicker never kicks more extra points than his
    team scored touchdowns and a side's points are exactly its touchdowns, kicks, conversions and safeties
    (sim_closing.json once had 14 endings with a try and no touchdown)."""
    captured = {}
    close, overtime = sim_closing.close, sim_draw._overtime

    def closing(*args, **kwargs):
        captured["after"] = out = close(*args, **kwargs)
        captured["sides"], captured["final"] = args[0], out
        return out

    def extra(*args, **kwargs):
        captured["final"] = out = overtime(*args, **kwargs)
        return out

    monkeypatch.setattr(sim_closing, "close", closing)
    monkeypatch.setattr(sim_draw, "_overtime", extra)
    w, rng = world(), np.random.default_rng(11)
    targets = (None, {"AAA": 31.0, "BBB": 13.0}, {"AAA": 10.0, "BBB": 13.0}, {"AAA": 17.0, "BBB": 17.0})
    for g in range(1500):
        result = sim_game.play(w, "AAA", "BBB", set(), np.random.default_rng(int(rng.integers(1 << 31))), HALF,
                               target=targets[g % len(targets)])
        offense, scored, _ = captured["final"][:3]
        _, _, _, returns, safeties, _, _ = captured["after"]
        for i, side in enumerate(captured["sides"]):
            counts, tds = scored[i][1], offense[i].tds + returns[i]
            assert counts["pat_att"] <= tds
            conversions = result.points[side.team] - 6 * tds - counts["pat_made"] - 3 * int(counts["made"].sum()) \
                - 2 * int(safeties[i])
            assert conversions >= 0 and conversions % 2 == 0 and conversions // 2 <= tds - counts["pat_att"]


def test_a_try_is_filed_with_its_touchdown_across_the_cut():
    game = _game([[4, 605, "AAA", "rush_td", 3], [4, 600, "AAA", "xp", 0], [4, 120, "BBB", "fg_0_39", 0]], 7, 3)
    before, inside, _ = sim_closing_fit.split(game, 600)
    assert before["AAA"] == {"rush_td": 1, "xp": 1} and not inside["AAA"] and inside["BBB"] == {"fg_0_39": 1}
