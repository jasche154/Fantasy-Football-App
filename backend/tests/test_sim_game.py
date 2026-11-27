"""sim_game / sim_draw: one simulated NFL game adds up exactly and behaves like football."""
from collections import defaultdict
from types import MappingProxyType

import numpy as np
import pytest

from services import sim_game
from services.replay_scoring import ScoringRules, scoring_settings
from services.scoring import scoring_for
from services.sim_world import Player, Team, World

HALF = scoring_for("half")
ROLES = {   # name: (position, targets, carries, starts)
    "QB": ("QB", 0.0, 0.12, 1.0), "QB2": ("QB", 0.0, 0.02, 0.05), "RB": ("RB", 0.10, 0.55, 0.0),
    "RB2": ("RB", 0.04, 0.25, 0.0), "WR": ("WR", 0.27, 0.01, 0.0), "WR2": ("WR", 0.20, 0.01, 0.0),
    "WR3": ("WR", 0.14, 0.0, 0.0), "TE": ("TE", 0.18, 0.0, 0.0), "TE2": ("TE", 0.07, 0.0, 0.0),
    "K": ("K", 0.0, 0.0, 1.0), "DST": ("DST", 0.0, 0.0, 0.0),
    # depth that fills every dressed slot (sim_game.ACTIVE_SLOTS), so no work goes to the "other" bucket
    "RB3": ("RB", 0.01, 0.04, 0.0), "WR4": ("WR", 0.03, 0.0, 0.0), "WR5": ("WR", 0.02, 0.0, 0.0),
    "TE3": ("TE", 0.01, 0.0, 0.0),
}
RATES = {"QB": (0.8, 2.5, 0.1, 4.5, 0.05), "RB": (0.78, 7.5, 0.04, 4.3, 0.03), "WR": (0.64, 12.5, 0.08, 6.0, 0.04),
         "TE": (0.7, 10.5, 0.07, 4.3, 0.05), "K": (0, 0, 0, 0, 0), "DST": (0, 0, 0, 0, 0)}


def pid(team: str, name: str) -> int:
    return (1000 if team == "AAA" else 2000) + list(ROLES).index(name) + 1


def world() -> World:
    players = {}
    for team in ("AAA", "BBB"):
        for name, (position, targets, carries, starts) in ROLES.items():
            catch, ypc, rec_td, ypr, rush_td = RATES[position]
            players[pid(team, name)] = Player(pid(team, name), position, team, targets, carries, starts, catch, ypc,
                                              rec_td, ypr, rush_td, 0.9 if name == "QB2" else 1.0, 0.023, 10.0)
    teams = {team: Team(team, 32.0, 27.0, 0.0, 0.0) for team in ("AAA", "BBB")}
    return World(MappingProxyType(players), MappingProxyType(teams))


def team_of(player_id: int) -> str:
    return "AAA" if player_id < 2000 else "BBB"


def test_the_passers_line_is_the_sum_of_his_receivers_lines():
    rng = np.random.default_rng(1)
    for _ in range(200):
        result = sim_game.play(world(), "AAA", "BBB", set(), rng, HALF)
        for team in ("AAA", "BBB"):
            lines = {p: g.box for p, g in result.players.items() if team_of(p) == team and g.usage is not None}
            qbs = (pid(team, "QB"), pid(team, "QB2"))
            catches = [b for p, b in lines.items() if p not in qbs]
            passers = [b for p, b in lines.items() if p in qbs]
            assert sum(b.rec_yds for b in catches) == sum(b.pass_yds for b in passers)
            assert sum(b.rec_td for b in catches) == sum(b.pass_td for b in passers)
            assert all(b.rec <= b.targets for b in lines.values())
            assert all(isinstance(v, (int, np.integer)) for b in lines.values() for v in b.__dict__.values())


def test_a_starter_who_leaves_hurt_hands_the_rest_of_the_game_to_the_backup_and_the_sums_still_hold():
    rng = np.random.default_rng(2)
    backup_threw = 0
    for _ in range(100):
        result = sim_game.play(world(), "AAA", "BBB", set(), rng, HALF, {pid("AAA", "QB"): 0.4})
        lines = {p: g.box for p, g in result.players.items() if team_of(p) == "AAA"}
        receiving = sum(b.rec_yds for p, b in lines.items() if p not in (pid("AAA", "QB"), pid("AAA", "QB2")))
        passing = lines[pid("AAA", "QB")].pass_yds + (lines[pid("AAA", "QB2")].pass_yds if pid("AAA", "QB2") in lines else 0)
        assert receiving == passing
        backup_threw += pid("AAA", "QB2") in lines
        assert result.players[pid("AAA", "QB")].usage.snap_pct == 40.0
    assert backup_threw > 90


def test_nobody_who_is_out_plays_and_the_dst_intercepts_exactly_what_the_other_qb_threw():
    rng = np.random.default_rng(3)
    out = {pid("AAA", "WR"), pid("BBB", "RB")}
    for _ in range(100):
        result = sim_game.play(world(), "AAA", "BBB", out, rng, HALF)
        assert not out & set(result.players)
        for team, other in (("AAA", "BBB"), ("BBB", "AAA")):
            thrown = sum(g.box.pass_int for p, g in result.players.items() if team_of(p) == other)
            dst_points = result.players[pid(team, "DST")].points
            assert dst_points >= 2 * thrown - 4         # every INT is worth 2 to the D/ST (worst points-allowed tier -4)
            assert dst_points == int(dst_points)


def test_dst_interceptions_equal_the_opposing_quarterbacks(monkeypatch):
    from services import sim_draw
    seen, real = [], sim_draw._dst_points

    def spy(sacks, ints, fumbles, tds, safeties, blocks, allowed):
        seen.append(ints)
        return real(sacks, ints, fumbles, tds, safeties, blocks, allowed)
    monkeypatch.setattr(sim_draw, "_dst_points", spy)
    rng = np.random.default_rng(4)
    for _ in range(50):
        seen.clear()
        result = sim_game.play(world(), "AAA", "BBB", set(), rng, HALF)
        thrown = {team: sum(g.box.pass_int for p, g in result.players.items() if team_of(p) == team) for team in ("AAA", "BBB")}
        assert seen == [thrown["BBB"], thrown["AAA"]]          # AAA's defense first, then BBB's


def test_an_injured_receivers_work_flows_to_his_teammates():
    healthy = sim_game.expected(world(), "AAA", "BBB", set(), HALF)
    without = sim_game.expected(world(), "AAA", "BBB", {pid("AAA", "WR")}, HALF)
    assert pid("AAA", "WR") not in without
    for name in ("WR2", "WR3", "TE", "RB"):
        assert without[pid("AAA", name)].box.targets > healthy[pid("AAA", name)].box.targets
    total = lambda lines: sum(e.box.targets for p, e in lines.items() if team_of(p) == "AAA")  # noqa: E731
    assert total(without) == pytest.approx(total(healthy), rel=1e-3)   # the closing window's few targets follow the lead


def test_a_hurt_back_s_carries_go_to_the_next_back_not_to_the_quarterback():
    healthy = sim_game.expected(world(), "AAA", "BBB", set(), HALF)
    without = sim_game.expected(world(), "AAA", "BBB", {pid("AAA", "RB")}, HALF)
    assert without[pid("AAA", "QB")].box.carries == pytest.approx(healthy[pid("AAA", "QB")].box.carries, rel=1e-3)
    assert without[pid("AAA", "RB2")].box.carries > 2 * healthy[pid("AAA", "RB2")].box.carries


def test_a_backup_qb_moves_every_receiver():
    healthy = sim_game.expected(world(), "AAA", "BBB", set(), HALF)
    backup = sim_game.expected(world(), "AAA", "BBB", {pid("AAA", "QB")}, HALF)
    assert backup[pid("AAA", "QB2")].box.pass_yds < healthy[pid("AAA", "QB")].box.pass_yds
    assert backup[pid("AAA", "WR")].box.rec_yds < healthy[pid("AAA", "WR")].box.rec_yds


def test_the_mean_of_many_games_is_the_expected_line():
    w, rng = world(), np.random.default_rng(5)
    expected = sim_game.expected(w, "AAA", "BBB", set(), HALF)
    points = defaultdict(float)
    draws = 3000
    for _ in range(draws):
        for p, g in sim_game.play(w, "AAA", "BBB", set(), rng, HALF).players.items():
            points[p] += g.points / draws
    for name in ("QB", "RB", "WR", "TE"):
        assert points[pid("AAA", name)] == pytest.approx(expected[pid("AAA", name)].points, rel=0.06)
    assert points[pid("AAA", "K")] == pytest.approx(expected[pid("AAA", "K")].points, rel=0.08)
    assert points[pid("AAA", "DST")] == pytest.approx(expected[pid("AAA", "DST")].points, abs=0.3)


def test_scores_vary_like_football_and_custom_scoring_counts():
    rng = np.random.default_rng(6)
    wr = [sim_game.play(world(), "AAA", "BBB", set(), rng, HALF).players[pid("AAA", "WR")].points for _ in range(500)]
    assert np.percentile(wr, 10) < 5 and np.percentile(wr, 90) > 15 and max(wr) < 60
    six = scoring_settings("half", ScoringRules(pass_td=6.0))
    four = sim_game.expected(world(), "AAA", "BBB", set(), HALF)[pid("AAA", "QB")]
    more = sim_game.expected(world(), "AAA", "BBB", set(), six)[pid("AAA", "QB")]
    assert more.points == pytest.approx(four.points + 2 * four.box.pass_td, abs=0.02)


def test_a_league_average_opponent_is_allowed():
    lines = sim_game.expected(world(), "AAA", None, set(), HALF)
    assert {team_of(p) for p in lines} == {"AAA"} and lines[pid("AAA", "DST")].points > 0


def test_a_thin_roster_leaves_depth_work_to_the_other_bucket_without_inflating_the_leads():
    full = world()
    thin = World(MappingProxyType({p: pl for p, pl in full.players.items()
                                   if not (team_of(p) == "AAA" and pl.position == "WR" and pl.targets < 0.1)}), full.teams)
    before = sim_game.expected(full, "AAA", "BBB", set(), HALF)
    after = sim_game.expected(thin, "AAA", "BBB", set(), HALF)
    lead = pid("AAA", "WR")
    assert after[lead].box.targets < 1.1 * before[lead].box.targets
    pool = sum(e.box.targets for p, e in after.items() if team_of(p) == "AAA")
    assert pool < full.teams["AAA"].targets - 1                  # some targets go to unpooled depth


def test_a_kicker_who_leaves_hurt_keeps_only_his_share_of_the_kicks():
    rng = np.random.default_rng(8)
    kicker = pid("AAA", "K")
    healthy = np.mean([sim_game.play(world(), "AAA", "BBB", set(), rng, HALF).players[kicker].points for _ in range(400)])
    hurt = np.mean([sim_game.play(world(), "AAA", "BBB", set(), rng, HALF, {kicker: 0.25}).players[kicker].points
                    for _ in range(400)])
    assert hurt == pytest.approx(0.25 * healthy, abs=0.6)


def test_each_passer_is_credited_only_touchdowns_among_the_catches_he_threw():
    from types import SimpleNamespace

    from services import sim_draw
    side = SimpleNamespace(qb=1, backup=2, qb_share=0.5)
    catches = np.array([1, 1, 2])
    offense = SimpleNamespace(catches=catches, rec_yds=np.array([30, 5, 20]), rec_td=np.array([1, 0, 1]), ints=0,
                              sack_fumbles=0)
    rng = np.random.default_rng(9)
    for _ in range(300):
        lines = sim_draw._passers(side, offense, rng)
        assert all(line["pass_td"] <= line["completions"] for line in lines.values())
        assert sum(line["pass_td"] for line in lines.values()) == 2
