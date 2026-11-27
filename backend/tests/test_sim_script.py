"""sim_script / sim_parts / sim_script_fit / sim_metrics_usage: a simulated game's plays follow the score, depth
players are in the game plan only some weeks, and every mean stays the projection's."""

import json

import numpy as np
import pytest

from services import sim_centre, sim_closing_fit, sim_draw, sim_game, sim_metrics_usage, sim_parts, sim_script, sim_script_fit
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


def test_kneel_downs_are_the_quarterback_who_finishes_the_games_and_no_window_play_goes_to_one_who_left():
    side = sim_draws.centred_sides(world(), "AAA", "BBB")[0]
    rng = np.random.default_rng(2)
    plan = sim_parts.plan(side, rng)
    at = side.rushers.index(side.qb)
    knelt = sim_parts.plays(side, plan, 0, 0, 1.0, rng, kneels=3)
    assert knelt.carries[at] == 3 and knelt.rush_yds[at] == round(3 * sim_script.kneel_yards()) and knelt.tds == 0
    w, qb, rb, wr = world(), pid("AAA", "QB"), pid("AAA", "RB"), pid("AAA", "WR")
    hurt = sim_centre.centred({"AAA": sim_game._side(w, "AAA", "BBB", set(), {qb: 0.5, rb: 0.5, wr: 0.5}),
                               "BBB": sim_game._side(w, "BBB", "AAA", set(), {})})["AAA"]
    backup = hurt.rushers.index(pid("AAA", "QB2"))
    events = [{"targets": 20, "carries": 20, "kneels": 3, "pass_td": 0, "rush_td": 0}] * 2
    for _ in range(200):
        plan = sim_parts.plan(hurt, rng)
        offense = [sim_draws.offense(hurt, 10, 10, rng)] * 2
        after, _, _ = sim_parts.window((hurt, hurt), offense, events, [plan, plan], np.ones(2), np.ones(2),
                                       np.zeros(2, dtype=int), np.zeros(2, dtype=int), rng)
        gained_c, gained_t = after[0].carries - offense[0].carries, after[0].targets - offense[0].targets
        for pid_ in (qb, rb):
            assert gained_c[hurt.rushers.index(pid_)] == 0
        assert gained_t[hurt.receivers.index(wr)] == 0 and gained_t[hurt.receivers.index(rb)] == 0
        assert gained_c[backup] == 3                                       # the backup finished: his kneels
    m = sim_game._side_means(hurt)
    assert m["kneels"][backup] > 0 and m["kneels"][hurt.rushers.index(qb)] == 0


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
            assert h.made.sum() <= 5 and h.pat_att <= o.tds


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
    (trail, lead, _), = table[7]
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


@pytest.mark.parametrize("target", [{"AAA": 20.0, "BBB": 20.0}, {"AAA": 27.0, "BBB": 17.0}, {"AAA": 17.0, "BBB": 27.0}])
def test_a_quarterbacks_projection_is_his_beliefs_kneels_and_all(target):
    """His belief carries count his kneels (box lines do), so his carries besides the kneels are his belief carries
    less his own expected kneels: at a pick'em, as a 10-point favourite and underdog his projected carries are his
    weight's share of the team's, his yards those at his per-carry rate, his TDs those before the cut at his rate."""
    side = sim_draws.centred_sides(world(), "AAA", "BBB", target)[0]
    qb = pid("AAA", "QB")
    at = side.rushers.index(qb)
    line = sim_game.expected(world(), "AAA", "BBB", set(), HALF, target=target)[qb].box
    team = sum(e.box.carries for p, e in sim_game.expected(world(), "AAA", "BBB", set(), HALF, target=target).items()
               if team_of(p) == "AAA")
    share = side.carry_w[at] / side.carry_w.sum()
    (_, h1), (_, h2) = sim_script.parts(side)
    window = sim_script.window_means(side)[1]
    assert sim_script.window_means(side)[2] > 0.1                          # he does kneel
    belief = share * (h1 + h2 + window + sim_script.window_means(side)[2])
    m = sim_game._side_means(side)                                        # the plays drawn, window TD runs aside
    assert m["carries"][at] == pytest.approx(belief, rel=1e-6)
    assert m["rush_yds"][at] == pytest.approx(belief * side.ypr[at], rel=1e-6)
    rate = side.rush_td[at] / sim_script.before_cut("carries")
    assert m["rush_td"][at] == pytest.approx(belief * (h1 + h2) / (h1 + h2 + window) * rate, rel=1e-6)
    # the projection adds the window's and overtime's TD runs, by TD rate (about 1% of his carries)
    assert line.carries == pytest.approx(belief, rel=0.015) and line.carries / team == pytest.approx(share, rel=0.02)


def test_a_season_cached_in_another_column_layout_is_downloaded_again(monkeypatch, tmp_path, caplog):
    """A cache written before a column was added would read every count after it one column off: such a season is
    left out of the cache and downloaded again, the other seasons kept."""
    good = {"season": 2024, "week": 1, "home": "AAA", "away": "BBB", "spread": 3.0,
            "AAA": [[0] * len(sim_script_fit.COUNTS)] * 3, "BBB": [[0] * len(sim_script_fit.COUNTS)] * 3}
    old = {**good, "season": 2023, "AAA": [[0] * (len(sim_script_fit.COUNTS) - 1)] * 3}
    path = tmp_path / "script_segments.json"
    path.write_text(json.dumps({"2023": [old], "2024": [good]}), encoding="utf-8")
    monkeypatch.setattr(sim_script_fit, "SEGMENTS_CACHE", path)
    fetched = []
    monkeypatch.setattr(sim_script_fit, "_download", lambda season: fetched.append(season) or [{**good, "season": season}])
    with caplog.at_level("WARNING", logger="services.sim_script_fit"):
        games = sim_script_fit.segments([2023, 2024])
    assert fetched == [2023] and [g["season"] for g in games] == [2023, 2024] and "column layout" in caplog.text
    assert sim_script_fit.segments([2024], refresh=True) and fetched == [2023, 2024]
    assert set(json.loads(path.read_text(encoding="utf-8"))) == {"2023", "2024"}     # a refresh keeps the rest


def test_a_failed_write_leaves_no_temp_file(monkeypatch, tmp_path):
    path = tmp_path / "sim_script.json"
    monkeypatch.setattr(sim_script_fit.os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        sim_script_fit.atomic_write(path, "{}")
    assert list(tmp_path.iterdir()) == []


def test_the_second_half_follows_the_first_halfs_score_at_a_fixed_line(monkeypatch):
    """At one pregame line, a side whose first half went better than the other's runs more and throws less after
    half time (the realized lead, not only the projected one, drives sim_script.second_half)."""
    sides = sim_draws.centred_sides(world(), "AAA", "BBB", {"AAA": 21.0, "BBB": 21.0})
    calls, real_half = [], sim_parts.half

    def half(side, plan, volume, *args, **kwargs):
        calls.append((side.team, np.array(volume, dtype=float)))
        return (out := real_half(side, plan, volume, *args, **kwargs), calls.append(out))[0]
    monkeypatch.setattr(sim_parts, "half", half)
    rng, rows = np.random.default_rng(6), []
    for _ in range(1500):
        calls.clear()
        sim_parts.before_cut(sides, [1.5, 1.5], rng)
        firsts = [calls[1].points, calls[3].points]                 # AAA's and BBB's first halves
        rows.append((firsts[0] - firsts[1], *calls[4][1]))         # AAA's second-half [targets, carries] means
    lead, targets, carries = np.array(rows).T
    assert np.corrcoef(lead, carries)[0, 1] > 0.9 and np.corrcoef(lead, targets)[0, 1] < -0.9


def test_a_depth_players_mean_share_through_the_game_plan_is_his_weights():
    """A player out of the plan some weeks gets his weight / q when in, so his mean share is his weight's (and the
    projection, which uses the weights, is his draws' mean)."""
    side = sim_draws.centred_sides(world(), "AAA", "BBB")[0]
    inputs = sim_parts.plan_inputs(side)
    depth = [i for i, at in enumerate(inputs.receivers) if at >= 0 and inputs.q[at] < 1.0]
    assert depth
    rng = np.random.default_rng(9)
    shares = np.mean([sim_parts.plan(side, rng).targets for _ in range(20_000)], axis=0)
    weights = side.target_w / side.target_w.sum()
    assert shares[depth] == pytest.approx(weights[depth], rel=0.04)


def test_leaving_hurt_never_changes_who_is_in_the_game_plan():
    """Who may sit out is worked out from the weights before anyone leaves the game: a lead receiver who leaves
    after a third of it is still always in the plan (he played until he left)."""
    w, wr = world(), pid("AAA", "WR")
    healthy, hurt = (sim_game._side(w, "AAA", "BBB", set(), exits) for exits in ({}, {wr: 0.3}))
    before, after = sim_parts.plan_inputs(healthy), sim_parts.plan_inputs(hurt)
    assert np.array_equal(before.q, after.q)
    assert after.q[after.receivers[hurt.receivers.index(wr)]] == 1.0
    rng = np.random.default_rng(3)
    assert min(sim_parts.plan(hurt, rng).targets[hurt.receivers.index(wr)] for _ in range(2000)) > 0


def test_a_quarterbacks_carry_share_is_his_full_strength_share_times_one_league_factor():
    """The same fitted factor in every game (belief and truth, week 1 or 18, whoever played before); a hurt back's
    carries still go to the next back, not to the quarterback."""
    w = world()
    qb = w.players[pid("AAA", "QB")]
    rushers = [p for p in w.roster("AAA") if p.position in ("RB", "WR", "TE") and p.carries > 0] + [qb]
    plain, scaled = (sim_game._carry_weights(rushers, qb, 0.0, {}, s)[-2] for s in (1.0, 1.25))
    assert scaled == pytest.approx(1.25 * plain)
    without = [p for p in rushers if p.player_id != pid("AAA", "RB")]
    assert sim_game._carry_weights(without, qb, 0.0, {}, 1.25)[-2] == pytest.approx(scaled)
    rows = [(2020 + i % 3, 0.1, 3.0, 25.0, 1.0) for i in range(30)] + [(2020 + i % 3, 0.2, 6.0, 25.0, 1.3) for i in range(30)]
    fitted = sim_script_fit.qb_share(rows)
    assert fitted["scale"] == pytest.approx(1.2) and fitted["check"]["held_out_sse"] < fitted["check"]["unscaled_sse"]


def test_with_players_leaving_hurt_every_players_projection_is_his_draws_mean():
    """A game with its QB, lead back and lead receiver leaving hurt: per player, the projected targets, carries and
    TDs (the window's plays and TDs, overtime's too, never to one who left) are the mean of the draws."""
    w, target = world(), {"AAA": 21.0, "BBB": 20.0}
    exits = {pid("AAA", "QB"): 0.5, pid("AAA", "RB"): 0.5, pid("AAA", "WR"): 0.6}
    projected = sim_game.expected(w, "AAA", "BBB", set(), HALF, exits=exits, target=target)
    sides = sim_centre.centred({"AAA": sim_game._side(w, "AAA", "BBB", set(), exits),
                                "BBB": sim_game._side(w, "BBB", "AAA", set(), {})}, target)
    rng, n, sums = np.random.default_rng(12), 6000, {}
    for _ in range(n):
        for p, g in sim_draw.draw_game((sides["AAA"], sides["BBB"]), rng, HALF).players.items():
            if team_of(p) == "AAA" and g.usage is not None:
                b = g.box
                row = sums.setdefault(p, np.zeros(4))
                row += (b.targets, b.carries, b.rec_td, b.rush_td)
    for name in ("RB", "RB2", "RB3", "WR", "WR2", "WR3", "TE", "QB", "QB2"):
        p = pid("AAA", name)
        drawn = sums.get(p, np.zeros(4)) / n
        box = projected[p].box if p in projected else None
        want = np.array([box.targets, box.carries, box.rec_td, box.rush_td]) if box else np.zeros(4)
        assert drawn[:2] == pytest.approx(want[:2], rel=0.05, abs=0.06), name
        assert drawn[2:] == pytest.approx(want[2:], rel=0.12, abs=0.012), name
