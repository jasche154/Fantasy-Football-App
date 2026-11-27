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
                                 ints=1, sack_fumbles=0, attempts=16)
    after = SimpleNamespace(**{**vars(regulation), "catches": np.array([6, 4, 0]), "rec_yds": np.array([72, 40, 0]),
                               "rec_td": np.array([1, 0, 0]), "attempts": 17})       # the overtime TD: a catch, an attempt
    for seed in range(40):
        lines = sim_draw._passers(side, after, np.random.default_rng(seed), regulation)
        assert lines[1]["pass_td"] == 0 and lines[2]["pass_td"] == 1
        assert lines[1]["pass_cmp"] + lines[2]["pass_cmp"] == 10 and lines[1]["pass_att"] + lines[2]["pass_att"] == 17
        assert all(line["pass_att"] >= line["pass_cmp"] + line["pass_int"] for line in lines.values())
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
    # review item 1: a rookie of the seeded draft (synthetic id) listed on today's depth chart, and the coming
    # season's real draftee, never join a second time
    rookie = replace(one.players[qbs[teams[3]][0]], player_id=1_000_000_123, name="Arch Manning Jr.")
    with_rookie = replace(one, players=MappingProxyType({**one.players, rookie.player_id: rookie}))
    twice = sim_depth.with_real_qbs(with_rookie, {teams[4]: [(900004, "arch manning"), (900005, "Real Draftee")]}, 5,
                                    rookies={900005})
    assert not {900004, 900005} & set(twice.players)
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
    players = {i: SimpleNamespace(position="WR") for i in range(1, 4)} | {9: SimpleNamespace(position="K")}
    projected = {(1, 4): 10.0, (1, 5): 12.0, (2, 4): 8.0, (2, 5): 9.0, (3, 4): 9.0, (3, 5): 0.0, (9, 5): 8.5}
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


def test_published_projections_take_the_positions_scale(belief, monkeypatch):
    """Item 27: the public table only, scaled per position (real sources project high)."""
    from services import sim_constants, sim_market
    monkeypatch.setitem(sim_constants._overrides, "market", {"projection_scale": {"QB": 1.1}})
    sim_constants.params.cache_clear()
    qb, wr = (next(p for p in belief.players.values() if p.position == pos).player_id for pos in ("QB", "WR"))
    assert sim_market.published({qb: 10.0, wr: 10.0, -1: 3.0}, belief) == {qb: pytest.approx(11.0), wr: 10.0, -1: 3.0}
    monkeypatch.undo()
    sim_constants.params.cache_clear()


def test_published_projections_start_at_the_join_level_and_fade_to_the_season_scale(belief, monkeypatch):
    """Item 5: the first simulated week after real ones continues the real sources' level, then fades."""
    from services import sim_constants, sim_market
    monkeypatch.setitem(sim_constants._overrides, "market", {"projection_scale": {"QB": 1.0}, "join_scale": {"QB": 1.2},
                                                             "join_fade": 0.5})
    sim_constants.params.cache_clear()
    qb = next(p for p in belief.players.values() if p.position == "QB").player_id
    levels = [sim_market.published({qb: 10.0}, belief, weeks)[qb] for weeks in (0, 1, 2)]
    assert levels == pytest.approx([12.0, 11.0, 10.5])
    assert sim_market.published({qb: 10.0}, belief)[qb] == pytest.approx(10.0)     # a season simulated whole
    monkeypatch.undo()
    sim_constants.params.cache_clear()


def test_projections_follow_the_line_only_partly(belief, monkeypatch):
    """Item 5: a weekly projection keeps line_follow of the line's move away from the team's typical points."""
    from services import sim_constants, sim_game, sim_lines, sim_market, sim_world
    game = next(g for g in fakes.fake_season().schedule.games if g.week == 9)
    home, away, scoring = game.home_team, game.away_team, sim_world.scoring_of(fakes.fake_season())
    line = {home: sim_lines.Line(margin=10.0, total=50.0)}
    full = sim_game.expected(belief, home, away, set(), scoring, target={home: 30.0, away: 20.0}, week=9)
    typical = sim_market._anchor(belief, home, away, set(), scoring, 9)    # this matchup at its typical points
    monkeypatch.setitem(sim_constants._overrides, "market", {"line_follow": {"QB": 0.5}})
    sim_constants.params.cache_clear()
    got = sim_market.projections(belief, SimpleNamespace(games=(game,)), 9, set(), scoring, line)
    qb = next(pid for pid in full if belief.players[pid].position == "QB" and belief.players[pid].team == home)
    assert got[qb] == pytest.approx((typical[qb] + full[qb].points) / 2, abs=0.01)
    monkeypatch.undo()
    sim_constants.params.cache_clear()


def test_passing_rates_regress_by_their_measured_keep_whatever_the_calibrated_share(monkeypatch):
    """The passing lead: receivers' yards per catch and catch rate and a QB's quality regress in full in August."""
    from services import sim_next_year
    model = {**sim_next_year.stored(), "efficiency_keep": {"ypc": 0.5, "WR ypc": 0.6, "catch": 0.5, "rec_td": 0.3,
                                                           "quality": 0.55},
             "efficiency_scale": {"WR": 0.0, "QB": 0.25}}
    monkeypatch.setattr(sim_next_year, "stored", lambda: model)
    assert sim_next_year.keep_of("WR", "ypc") == 0.6 and sim_next_year.keep_of("TE", "ypc") == 0.5
    assert sim_next_year.keep_of("WR", "catch") == 0.5 and sim_next_year.keep_of("QB", "quality") == 0.55
    assert sim_next_year.keep_of("WR", "rec_td") == 1.0                          # the calibrated share (0) applies
    assert sim_next_year.keep_of("WR", "rec_td", scaled=False) == 0.3


def test_the_keep_slope_reads_full_seasons_of_the_wanted_position():
    from services import sim_next_year_fit as fit
    norm = sim_world.params()["positions"]["WR"]["ypc"]
    def row(position, before, after, games=16):
        return {"position": position, "games": [games, games], "volume": [100.0, 100.0], "ypc": [before * norm, 0],
                "raw": {"rec": 80.0, "rec_yds": 80.0 * after * norm}, "passer": [1.0, 1.0]}
    rows = [row("WR", 1.2, 1.1), row("WR", 0.8, 0.9), row("TE", 1.2, 1.2), row("WR", 1.5, 0.5, games=2)]
    assert fit._slope(rows, "ypc", lambda p: p == "WR") == pytest.approx(0.5)     # the 2-game season is left out


def test_passing_rows_read_the_qb_lines():
    from services import sim_metrics_shape
    from services.replay_box import BoxLine
    players = {1: SimpleNamespace(position="QB"), 2: SimpleNamespace(position="QB"), 3: SimpleNamespace(position="WR")}
    box = {(1, 1): BoxLine(pass_yds=300), (1, 2): BoxLine(pass_yds=200), (2, 1): BoxLine(pass_yds=100),
           (3, 1): BoxLine(rec_yds=90, targets=9, rec=6)}
    data = SimpleNamespace(box=box, players=players, team_at=lambda pid, w: "AAA" if pid != 2 else "BBB")
    out = sim_metrics_shape.passing(data)
    assert out["pass yds #1"] == 500 and out["team pass yds max"] == 500 and out["team-game pass yds mean"] == 200


def test_a_teams_volume_leans_against_its_efficiency_draw():
    """The passing lead: real teams that pass well do not also pass more (r = -0.28); the truth's volume draw
    follows the offense's shared efficiency draw at volume_eff_rho."""
    team = sim_world.Team("AAA", 32.0, 26.0, 0.0, 0.0)
    knobs = {**sim_talent.settings(), "volume_eff_rho": -0.5, "volume_sd": 0.1}
    z = np.zeros(4)
    assert sim_talent._team(team, z, knobs, eff_z=2.0).targets < sim_talent._team(team, z, knobs, eff_z=-2.0).targets
    assert sim_talent._team(team, z, {**knobs, "volume_eff_rho": 0.0}, eff_z=2.0).targets == pytest.approx(
        32.0 * np.exp(-0.1 ** 2 / 2))


def _receiver_vs(belief, pass_def: float) -> float:
    from services import sim_lines, sim_market, sim_world
    data = fakes.fake_season()
    game = next(g for g in data.schedule.games if g.week == 9)
    home, away, scoring = game.home_team, game.away_team, sim_world.scoring_of(data)
    teams = {**belief.teams, away: replace(belief.teams[away], pass_def=pass_def)}
    world = replace(belief, teams=MappingProxyType(teams))
    got = sim_market.projections(world, SimpleNamespace(games=(game,)), 9, set(), scoring,
                                 {home: sim_lines.Line(margin=3.0, total=46.0)})
    wr = max((pid for pid in got if world.players[pid].team == home and world.players[pid].position == "WR"),
             key=lambda pid: world.players[pid].targets)
    return got[wr]


def test_a_receivers_projection_follows_the_pass_defense_he_faces(belief):
    """M3: the projection's anchor is this matchup at the team's typical points, so with the line and everything
    else equal a receiver projects lower against the best pass defense than against the worst (an anchor with no
    opponent left 70% of his projection blind to it: 4.2% apart here, only through the line's share, against 6.9%
    now)."""
    best, worst = _receiver_vs(belief, 0.8), _receiver_vs(belief, 1.2)
    assert best < 0.945 * worst
