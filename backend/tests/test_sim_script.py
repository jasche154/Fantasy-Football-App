"""sim_script / sim_parts / sim_script_fit / sim_metrics_usage: a simulated game's plays follow the score, depth
players are in the game plan only some weeks, and every mean stays the projection's."""
from dataclasses import replace

import numpy as np
import pytest

from services import sim_closing_fit, sim_draw, sim_game, sim_metrics_usage, sim_parts, sim_script, sim_script_fit
from tests import replay_fakes as fakes, sim_draws
from tests.test_sim_game import HALF, pid, team_of, world


def test_the_game_plan_keeps_every_weight_on_average_and_sits_a_depth_player_out_entirely():
    weights = np.array([0.40, 0.25, 0.12, 0.08, 0.05, 0.10])       # the last: the "other" bucket, kept
    q = np.array([1.0, 1.0, 0.8, 0.6, 0.5, 1.0])
    fixed = np.array([False, False, False, False, False, True])
    rng = np.random.default_rng(3)
    drawn = [sim_script.plan_weights(weights, q, rng.random(6) < q, fixed) for _ in range(40_000)]
    assert np.mean(drawn, axis=0) == pytest.approx(weights, rel=0.01)
    assert all(abs(d.sum() - weights.sum()) < 1e-12 for d in drawn[:100])
    out = sim_script.plan_weights(weights, q, np.array([True, True, False, True, True, True]), fixed)
    assert out[2] == 0.0 and out[3] == pytest.approx(0.08 / 0.6) and out[5] == 0.10


def test_lead_players_are_always_in_the_plan_and_small_roles_are_lumpy():
    assert sim_script.in_plan(16.0, "RB") == 1.0 and sim_script.in_plan(0.8, "WR") < 0.7
    assert sim_script.in_plan(3.5, "RB") < sim_script.in_plan(3.5, "WR")       # backs are lumpier (fitted)
    assert sim_script.in_plan(1.0, "QB") == 1.0                                 # no curve: always in
    side = sim_draws.centred_sides(world(), "AAA", "BBB")[0]
    rng = np.random.default_rng(1)
    lead = side.rushers.index(pid("AAA", "RB"))
    third = side.rushers.index(pid("AAA", "RB3"))
    plans = [sim_parts.plan(side, rng) for _ in range(3000)]
    assert min(p.carries[lead] for p in plans) > 0.2                             # a quarter of the carries: never out
    assert np.mean([p.carries[third] == 0 for p in plans]) > 0.3                 # a third back: often not in it


def test_the_second_half_throws_more_when_behind_and_runs_more_when_ahead():
    behind, ahead = sim_script.second_half("targets", 0.0, -14.0), sim_script.second_half("targets", 0.0, 14.0)
    assert behind > 1.1 > 1.0 > 0.9 > ahead
    assert sim_script.second_half("carries", 0.0, 14.0) > 1.05 > 0.95 > sim_script.second_half("carries", 0.0, -14.0)


def test_drawn_games_follow_the_score_and_average_the_projected_volume():
    """Over many draws of one game a team's carries rise and its targets fall with its final margin, and its mean
    targets, carries and the QB's carries (kneels included) are the projection's."""
    w = world()
    target = {"AAA": 27.0, "BBB": 20.0}
    expected = sim_game.expected(w, "AAA", "BBB", set(), HALF, target=target)
    rows = []
    for r in sim_draws.games(w, "AAA", "BBB", HALF, 4000, 8, target):
        mine = [g.box for p, g in r.players.items() if team_of(p) == "AAA"]
        rows.append((r.points["AAA"] - r.points["BBB"], sum(b.targets for b in mine), sum(b.carries for b in mine),
                     r.players[pid("AAA", "QB")].box.carries))
    margin, targets, carries, qb = np.array(rows, dtype=float).T
    assert np.corrcoef(margin, carries)[0, 1] > 0.3 and np.corrcoef(margin, targets)[0, 1] < -0.15
    team = lambda key: sum(getattr(e.box, key) for p, e in expected.items() if team_of(p) == "AAA")  # noqa: E731
    assert targets.mean() == pytest.approx(team("targets"), rel=0.015)
    assert carries.mean() == pytest.approx(team("carries"), rel=0.015)
    assert qb.mean() == pytest.approx(expected[pid("AAA", "QB")].box.carries, rel=0.04)


def test_kneel_downs_are_the_starting_qbs_carries_and_never_one_who_left_hurt():
    side = sim_draws.centred_sides(world(), "AAA", "BBB")[0]
    rng = np.random.default_rng(2)
    plan = sim_parts.plan(side, rng)
    at = side.rushers.index(side.qb)
    knelt = sim_parts.plays(side, plan, 0, 0, 1.0, rng, kneels=3)
    assert knelt.carries[at] == 3 and knelt.rush_yds[at] == round(3 * sim_script.kneel_yards()) and knelt.tds == 0
    hurt_side = replace(side, exited=frozenset({side.qb}))
    events = [{"targets": 0, "carries": 3, "kneels": 3, "pass_td": 0, "rush_td": 0}] * 2
    offense = [sim_draws.offense(hurt_side, 10, 10, rng), sim_draws.offense(hurt_side, 10, 10, rng)]
    after, _, carries = sim_parts.window((hurt_side, hurt_side), offense, events, [plan, plan], np.ones(2), np.ones(2),
                                         np.zeros(2, dtype=int), np.zeros(2, dtype=int), rng)
    assert after[0].carries[at] == offense[0].carries[at] and carries.tolist() == [0, 0]


def test_the_window_adds_a_real_endings_plays_but_no_score():
    side = sim_draws.centred_sides(world(), "AAA", "BBB")[0]
    rng = np.random.default_rng(5)
    plan = sim_parts.plan(side, rng)
    before = [sim_draws.offense(side, 20, 15, rng)] * 2
    ending = {"targets": 9, "carries": 2, "kneels": 0, "pass_td": 1, "rush_td": 0}
    after, n_targets, n_carries = sim_parts.window((side, side), before, [ending, ending], [plan, plan], np.ones(2),
                                                   np.ones(2), np.zeros(2, dtype=int), np.zeros(2, dtype=int), rng)
    added = int(after[0].targets.sum() - before[0].targets.sum())
    assert added == n_targets[0] and abs(added - 8 * sim_script.window_scale(side)[0]) < 1      # its TD catch aside
    assert after[0].tds == before[0].tds and after[0].ints == before[0].ints and after[0].sacks == before[0].sacks


def test_a_teams_halves_add_up_and_attempts_cover_completions_and_interceptions():
    sides = sim_draws.centred_sides(world(), "AAA", "BBB", {"AAA": 24.0, "BBB": 21.0})
    rng = np.random.default_rng(4)
    for _ in range(300):
        halves, plans, _, _ = sim_parts.before_cut(sides, [1.5, 1.5], rng)
        for h in halves:
            o = h.offense
            assert o.attempts >= int(o.catches.sum()) + o.ints and (o.catches <= o.targets).all()
            assert h.made.sum() <= 5 and h.pat_att + 0 <= o.tds


def test_drives_split_between_the_halves_keep_a_whole_games_count():
    side = sim_draws.centred_sides(world(), "AAA", "BBB", {"AAA": 31.0, "BBB": 13.0})[0]
    volumes = sim_script.parts(side)
    share = float(volumes[0].sum() / volumes.sum())
    first, second = sim_parts.drives_before_cut(side, volumes, share)
    per = sim_parts._per_play(side)
    tds = volumes[:, 0] * per[1] + volumes[:, 1] * per[3]
    assert first + second == max(5, int(np.ceil(tds.sum())) + 1) and first >= tds[0] and second >= tds[1]


def _row(qtr: int, left: int, team: str = "AAA", **kw) -> dict:
    base = {"game_id": "g", "week": 3, "home_team": "AAA", "away_team": "BBB", "spread_line": 3.5, "qtr": qtr,
            "game_seconds_remaining": left, "play_id": 0, "posteam": team, "defteam": "BBB" if team == "AAA" else "AAA",
            "posteam_score": 0, "defteam_score": 0, "play_type": "run", "pass_attempt": 0, "rush_attempt": 0, "sack": 0,
            "receiver_player_id": None, "two_point_attempt": 0, "qb_kneel": 0, "qb_spike": 0, "qb_scramble": 0,
            "rusher_player_id": None, "passer_player_id": None, "yards_gained": 0}
    return {**base, **kw}


def test_the_fit_reads_each_parts_plays_the_way_the_box_score_counts_them():
    rows = [_row(1, 3500, play_type="pass", pass_attempt=1, receiver_player_id="w", passer_player_id="q"),
            _row(1, 3400, rush_attempt=1, rusher_player_id="r"),
            _row(2, 2000, play_type="no_play", rush_attempt=1, rusher_player_id="r"),          # wiped by a penalty
            _row(3, 1700, team="BBB", posteam_score=7, defteam_score=3, rush_attempt=1, rusher_player_id="x"),
            _row(4, 500, posteam_score=3, defteam_score=7, play_type="pass", pass_attempt=1, qb_spike=1,
                 passer_player_id="q"),
            _row(4, 400, team="BBB", posteam_score=7, defteam_score=3, play_type="qb_kneel", rush_attempt=1, qb_kneel=1,
                 rusher_player_id="x", yards_gained=-1),
            _row(4, 300, rush_attempt=1, two_point_attempt=1, rusher_player_id="r"),          # a try: not a play
            _row(5, 100, rush_attempt=1, rusher_player_id="r")]                               # overtime: left out
    game = sim_script_fit.game_counts(rows, 2024)
    parts = [dict(zip(sim_script_fit.COUNTS, p)) for p in game["AAA"]]
    assert (parts[0]["targets"], parts[0]["carries"], parts[0]["attempts"]) == (1, 1, 1)
    assert parts[1]["carries"] == 0 and parts[2]["margin"] == -4
    assert (parts[2]["attempts"], parts[2]["targets"], parts[2]["spikes"]) == (1, 0, 1)
    other = [dict(zip(sim_script_fit.COUNTS, p)) for p in game["BBB"]]
    assert (other[2]["carries"], other[2]["kneels"], other[2]["qb_carries"], other[2]["kneel_yards"]) == (1, 1, 1, -1)
    assert sim_script_fit.window_counts(game, "BBB") == {"targets": 0, "carries": 1, "kneels": 1}


def test_the_participation_fit_finds_a_lumpy_role_and_scores_better_held_out():
    rng = np.random.default_rng(0)
    rows = []
    for season in (2020, 2021, 2022):
        for _ in range(3000):
            mu = float(rng.uniform(0.3, 12.0))
            q = 1 - 0.8 * np.exp(-mu / 2.5)
            rows.append((season, "RB", mu, int(rng.poisson(mu / q)) if rng.random() < q else 0))
    curve = sim_script_fit.participation(rows)["RB"]
    assert curve["c"] == pytest.approx(0.8, abs=0.1) and curve["scale"] == pytest.approx(2.5, abs=0.75)
    assert curve["check"]["held_out_loss"] < curve["check"]["without"]


def test_the_closing_table_carries_each_endings_plays():
    game = {"season": 2020, "week": 1, "home": "AAA", "away": "BBB", "home_score": 10, "away_score": 3,
            "plays": [[1, 3000, "AAA", "fg_0_39", 0], [2, 2000, "AAA", "pass_td", 10], [2, 2000, "AAA", "xp", 0],
                      [3, 1000, "BBB", "fg_0_39", 0]],
            "plays_in_window": {"AAA": {"targets": 2, "carries": 7, "kneels": 3},
                                "BBB": {"targets": 11, "carries": 1, "kneels": 0}}}
    table = sim_closing_fit.endings([game], sim_closing_fit.WINDOW)
    (trail, lead), = table[7]
    events = sim_closing_fit.EVENTS
    assert [trail[events.index(k)] for k in ("targets", "carries", "kneels")] == [11, 1, 0]
    assert [lead[events.index(k)] for k in ("targets", "carries", "kneels")] == [2, 7, 3]


def test_usage_rows_read_the_box_and_the_scores():
    data = fakes.fake_season()
    rows = sim_metrics_usage.usage(data, range(1, data.last_week + 1))
    assert rows["usage carries"] > 0 and rows["usage players with a target"] >= 1
    assert -1 <= rows["usage corr(margin, carries)"] <= 1
    assert sum(rows[f"usage target share #{i}"] for i in range(1, 6)) + rows["usage target share #6+"] == pytest.approx(1.0)
    assert sim_metrics_usage.usage(data, []) == {}


def test_projections_read_nothing_the_draws_change():
    """The game plan, the halves and the window are drawn on the sides only: drawing games leaves every projection
    (sim_game.expected, the belief's) as it was."""
    w = world()
    before = sim_game.expected(w, "AAA", "BBB", set(), HALF, target={"AAA": 24.0, "BBB": 17.0})
    list(sim_draws.games(w, "AAA", "BBB", HALF, 50, 1, {"AAA": 24.0, "BBB": 17.0}))
    sim_draw.draw_game(sim_draws.centred_sides(w, "AAA", "BBB"), np.random.default_rng(0), HALF)
    assert sim_game.expected(w, "AAA", "BBB", set(), HALF, target={"AAA": 24.0, "BBB": 17.0}) == before
