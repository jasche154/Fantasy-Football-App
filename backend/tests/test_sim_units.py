"""Small hand-built checks of simulator helpers the season-level tests only reach indirectly (review of #66)."""
from dataclasses import replace
from types import MappingProxyType, SimpleNamespace

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


def test_no_team_plays_a_game_without_a_quarterback(monkeypatch):
    """Review item 20: every team down to one QB and QBs hurt often: the pooled backup is always under centre."""
    from services import sim_depth, sim_injuries
    data = fakes.fake_season()
    first = {}
    for pid, p in sorted(data.players.items(), key=lambda kv: (kv[1].adp or 999, kv[0])):
        if p.position == "QB":
            first.setdefault(data.team_at(pid, 5), pid)
    gone = {pid for pid, p in data.players.items() if p.position == "QB" and pid not in first.values()}
    def keep(table):
        return MappingProxyType({k: v for k, v in table.items() if k[0] not in gone})
    one = replace(data, players=MappingProxyType({k: v for k, v in data.players.items() if k not in gone}),
                  box=keep(data.box), actual=keep(data.actual), projected=keep(data.projected), injuries=keep(data.injuries),
                  team_by_week=MappingProxyType({k: v for k, v in data.team_by_week.items() if k not in gone}))
    real_hazard = sim_injuries.hazard
    monkeypatch.setattr(sim_injuries, "hazard", lambda position: 0.4 if position == "QB" else real_hazard(position))
    for seed in range(3):
        sim = sim_season.simulate(one, seed, 6)
        assert sim_metrics_games.tails(sim, range(6, sim.last_week + 1))["team-games without a QB line"] == 0
        pooled = [pid for pid in sim.players if pid >= sim_depth.BACKUP_QB_ID_BASE]
        assert len(pooled) == len({t for t, _ in sim.schedule.opponents})
        assert any((pid, w) in sim.box and sim.box[(pid, w)].pass_yds > 0 for pid in pooled for w in range(6, 18))


def test_a_teams_real_qbs_join_the_pool_and_only_a_short_team_gets_a_generic():
    """The user's ruling on item 20: today's real QB rooms first (moves and retirements on top), then a generic
    backup only where a team is still short of two."""
    from services import sim_depth
    data = fakes.fake_season()
    qbs = {}
    for pid, p in data.players.items():
        if p.position == "QB":
            qbs.setdefault(data.team_at(pid, 5), []).append(pid)
    one = replace(data, players=MappingProxyType({pid: p for pid, p in data.players.items()
                                                  if p.position != "QB" or pid == qbs[data.team_at(pid, 5)][0]}))
    teams = sorted({t for t, _ in data.schedule.opponents})
    rooms = {teams[0]: [(qbs[teams[0]][0], "Starter"), (900001, "Real Backup")],
             teams[1]: [(900002, "Retired Backup")], teams[2]: [(900003, "Moved Backup")]}
    added = sim_depth.with_real_qbs(one, rooms, 5, {900002: None, 900003: teams[0]})
    assert {pid: (p.name, p.adp_team) for pid, p in added.players.items() if pid >= 900000} == {
        900001: ("Real Backup", teams[0]), 900003: ("Moved Backup", teams[0])}
    assert added.projected[(900001, 5)] > 0                                     # on the board: a manager can add him
    pooled = sim_depth.with_backup_qbs(added, 5)
    generic = {p.adp_team for pid, p in pooled.players.items() if pid >= sim_depth.BACKUP_QB_ID_BASE}
    assert teams[0] not in generic and set(teams[1:]) <= generic


def test_an_emergency_start_is_the_pooled_qb_else_the_one_due_back_soonest():
    from services import sim_depth
    world = sim_world.World(MappingProxyType({1: sim_world.Player(1, "QB", "AAA"), 2: sim_world.Player(2, "QB", "AAA"),
                                              3: sim_world.Player(3, "QB", "BBB"),
                                              sim_depth.BACKUP_QB_ID_BASE: sim_world.Player(sim_depth.BACKUP_QB_ID_BASE, "QB", "BBB")}),
                            MappingProxyType({t: sim_world.Team(t, 30.0, 25.0, 0.0, 0.0) for t in ("AAA", "BBB")}))
    assert sim_depth.emergency(world, {1, 2, 3}, {1: 4, 2: 1, 3: 2}) == {1, 3}
    assert sim_depth.emergency(world, {1}, {1: 4}) == {1}                         # a healthy QB left: nobody called


def test_the_depth_discount_moves_work_to_a_teams_top_receivers_and_keeps_its_total(monkeypatch):
    from services import sim_constants, sim_next_year
    monkeypatch.setitem(sim_constants._overrides, "talent", {"depth_targets": 0.5})
    sim_constants.params.cache_clear()
    new = {i: sim_world.Player(i, "WR", "AAA", targets=w) for i, w in enumerate((0.3, 0.2, 0.15, 0.1, 0.1, 0.1, 0.05), 1)}
    before = dict(new)
    sim_next_year._concentrate(new)
    assert sum(p.targets for p in new.values()) == pytest.approx(sum(p.targets for p in before.values()))
    assert new[6].targets == pytest.approx(0.05) and new[7].targets == pytest.approx(0.025)
    assert new[1].targets / new[2].targets == pytest.approx(1.5)                 # the top five in proportion
    monkeypatch.undo()
    sim_constants.params.cache_clear()


def test_a_whole_season_draws_a_veterans_role_and_a_rookies_by_its_own_residual(monkeypatch):
    from services import sim_next_year
    model = {**sim_next_year.stored(), "role_sd": {"WR": 0.4}, "rookies": {"WR": {"role_sd": 0.6}}}
    monkeypatch.setattr(sim_next_year, "stored", lambda: model)
    knobs = {**sim_talent.settings(), "season_roles": 1.0}
    veteran, rookie = sim_world.Player(1, "WR", "AAA", targets=0.2, games=17), sim_world.Player(2, "WR", "AAA", targets=0.2)
    assert sim_talent._season_role(veteran, 1.0, knobs).targets > 0.2 > sim_talent._season_role(veteran, -1.0, knobs).targets
    assert sim_talent._season_role(rookie, 1.0, knobs).targets > sim_talent._season_role(veteran, 1.0, knobs).targets
    model["rookies"] = {}
    assert sim_talent._season_role(rookie, 1.0, knobs) == rookie


def test_season_shape_and_standings_read_the_box_and_the_scores():
    from services import sim_metrics_shape
    from services.replay_box import BoxLine
    players = {1: SimpleNamespace(position="WR"), 2: SimpleNamespace(position="TE"), 3: SimpleNamespace(position="RB")}
    box = {(1, w): BoxLine(targets=10, rec=7, rec_yds=70) for w in range(1, 18)}
    box |= {(2, w): BoxLine(targets=6, rec=4, rec_yds=40) for w in range(1, 18)}
    box |= {(3, w): BoxLine(carries=20, rush_yds=60, targets=4, rec=3, rec_yds=20) for w in range(1, 18)}
    data = SimpleNamespace(box=box, players=players, team_at=lambda pid, w: "AAA")
    out = sim_metrics_shape.shape(data)
    assert out["1000-yd receivers"] == 1 and out["1000-yd rushers"] == 1 and out["WR rec yds #1"] == 1190
    assert out["top WR target share"] == pytest.approx(0.5) and out["top-5 target share"] == pytest.approx(1.0)
    games = [_game(1, "H", "A", 0, 40), _game(2, "A", "H", 0, 40)]
    scores = {("H", 1): 20, ("A", 1): 17, ("H", 2): 30, ("A", 2): 10}
    table = sim_metrics_shape.standings(SimpleNamespace(schedule=SimpleNamespace(games=games), scores=scores))
    assert table["best record (wins)"] == 2 and table["worst record (wins)"] == 0
    assert table["games by 1-3 %"] == 50 and table["games by 8 or fewer %"] == 50


def test_implied_opinions_carry_the_last_real_ranks_into_the_first_simulated_week(belief):
    """Item 28: seeded from week W - 1's real ranks, the experts' first ranks (no fresh noise) repeat them."""
    from services import sim_market
    from services.scoring import scoring_for
    worth = sim_market.values(belief, scoring_for("half"), 8)
    wrs = sorted((pid for pid in worth if belief.players[pid].position == "WR" and worth[pid] > 0), key=lambda p: -worth[p])[1:11]
    swapped = [wrs[i ^ 1] for i in range(len(wrs))]                     # the experts swap each neighbouring pair
    real = {(pid, WEEK - 1): rank for rank, pid in enumerate(swapped, start=1)}
    known = SimpleNamespace(expert_ranks=real)
    z = sim_market.implied_opinions(belief, known, WEEK - 1, worth)
    ranks = sim_market.expert_ranks(belief, wrs, WEEK, np.random.default_rng(0), scoring_for("half"), 8, opinions=z, worth=worth)
    assert sorted(wrs, key=lambda p: ranks[(p, WEEK)]) == swapped
    assert all(abs(v) <= sim_market.OPINION_CAP for v in z.values())


def test_join_and_churn_rows_read_projections_ranks_and_tags():
    from services import sim_metrics_join
    players = {i: SimpleNamespace(position="WR") for i in range(1, 4)}
    projected = {(1, 4): 10.0, (1, 5): 12.0, (2, 4): 8.0, (2, 5): 9.0, (3, 4): 9.0, (3, 5): 0.0}
    ranks = {(1, 4): 1, (2, 4): 2, (3, 4): 3, (1, 5): 2, (2, 5): 1, (3, 5): 3}
    data = SimpleNamespace(players=players, projected=projected, expert_ranks=ranks, injuries={(3, 5): fakes.Injury("O")},
                           box={(1, 5): None}, scores={("AAA", 5): 3, ("BBB", 5): 24}, team_at=lambda pid, w: "AAA")
    join = sim_metrics_join.join(data, 5)
    assert join["join proj ratio WR"] == pytest.approx(21 / 18) and join["join expert churn WR"] == 1.0
    churn = sim_metrics_join.churn(data, [4, 5])
    assert churn["proj churn WR median"] == 1.5
    absent = sim_metrics_join.absences(data, [5])
    assert absent["untagged no-shows / week"] == 1 and absent["team score <= 6 %"] == 50   # player 2: no box, no tag
    data.projected[(1, 5)] = 0.0
    assert sim_metrics_join.absences(data, [5])["untagged zero projections / week"] == 1   # player 1 played
