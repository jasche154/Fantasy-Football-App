"""sim_lines: the pregame line of a simulated game, and how sim_game / sim_draw / sim_season use it."""
from dataclasses import replace
from types import MappingProxyType

import numpy as np
import pytest

from services import replay_blob, sim_centre, sim_closing_means, sim_draw, sim_game, sim_lines, sim_market, sim_season, sim_world
from tests import replay_fakes as fakes, sim_draws

WEEK = 9
SEED = 5


@pytest.fixture(scope="module")
def data():
    return fakes.fake_season()


@pytest.fixture(scope="module")
def belief(data):
    return sim_world.estimate(data, WEEK)


def _results_changed(data, week: int):
    """Everything after `week` changed (future_changed), and `week`'s own results and scores too; its pre-game
    projections and tags (known before it) stay."""
    later = fakes.future_changed(data, week)
    box = {k: replace(b, rec_yds=b.rec_yds + 40, rec_td=b.rec_td + 1) if k[1] == week else b for k, b in later.box.items()}
    scores = {k: v + 21 if k[1] == week else v for k, v in later.scores.items()}
    return replace(fakes.patched(later, actual={k: v + 9 for k, v in later.actual.items() if k[1] == week}),
                   box=MappingProxyType(box), scores=MappingProxyType(scores))


def _out(data, week):
    return {pid for (pid, w), tag in data.injuries.items() if w == week and tag.hard}


def test_ratings_find_the_team_that_scores_and_the_one_that_allows():
    games = [(w, "A", "B", 35, 10) for w in range(1, 6)] + [(w, "C", "D", 20, 20) for w in range(1, 6)]
    rated = sim_lines.ratings(games, 6)
    assert rated.teams["A"].offense > 3 and rated.teams["B"].defense > 3
    assert rated.teams["B"].offense < 0 and rated.teams["A"].defense < 0
    assert abs(rated.teams["C"].offense) < abs(rated.teams["A"].offense)
    assert rated.teams == sim_lines.ratings(games + [(6, "A", "B", 0, 60)], 6).teams       # week 6 is not seen yet


def test_last_season_is_carried_in_regressed_and_fades():
    last = sim_lines.carry_over([(w, "A", "B", 38, 10) for w in range(1, 18)])
    full = sim_lines.ratings([(w, "A", "B", 38, 10) for w in range(1, 18)], 18).teams["A"].offense
    assert 0 < last["A"].offense < full
    assert sim_lines.ratings([], 1, last).teams["A"].offense == pytest.approx(last["A"].offense)
    evened = sim_lines.ratings([(w, "A", "B", 20, 20) for w in range(1, 10)], 10, last).teams["A"].offense
    assert 0 < evened < last["A"].offense


def test_the_line_favours_the_better_team_and_the_home_side(data, belief):
    lines = sim_lines.week_lines(data.as_of(WEEK), WEEK, belief, _out(data, WEEK))
    games = [g for g in data.schedule.games if g.week == WEEK]
    assert set(lines) == {g.home_team for g in games}
    assert all(sim_lines.MIN_TOTAL <= line.total < 80 and abs(line.margin) < 30 for line in lines.values())
    parts = sim_lines.features(sim_lines.Rated(22.0, {}), "A", "B", (22.0, 22.0), (7, 7), ((1.0, 1.0), (1.0, 1.0)))
    assert sim_lines.predict(parts).margin == pytest.approx(sim_lines.stored()["margin"]["const"], abs=0.01)
    assert sim_lines.stored()["margin"]["const"] > 0                         # home field
    stronger = sim_lines.features(sim_lines.Rated(22.0, {}), "A", "B", (27.0, 20.0), (7, 7), ((1.1, 1.0), (1.0, 1.0)))
    assert sim_lines.predict(stronger).margin > sim_lines.predict(parts).margin


def test_the_line_reads_nothing_from_the_week_on(data):
    """No hindsight: the line of week W is the same whatever happens from week W on."""
    changed = _results_changed(data, WEEK)
    assert changed.scores != data.scores
    mine = sim_lines.week_lines(data.as_of(WEEK), WEEK, sim_world.estimate(data, WEEK), _out(data, WEEK))
    theirs = sim_lines.week_lines(changed.as_of(WEEK), WEEK, sim_world.estimate(changed, WEEK), _out(changed, WEEK))
    assert mine == theirs
    earlier = _results_changed(data, WEEK - 1)                  # a week it may read: the line moves
    assert sim_lines.week_lines(earlier.as_of(WEEK), WEEK, sim_world.estimate(earlier, WEEK), _out(earlier, WEEK)) != mine


def test_a_centred_side_hits_any_reasonable_target(data, belief):
    """The closed-form solve reaches every target from a weak offense to a blowout (overtime and the closing window
    included; the window alone averages about 3 points, so 6 is the lowest target)."""
    worst = 0.0
    for game in (g for g in data.schedule.games if g.week == WEEK):
        home, away = game.home_team, game.away_team
        for wanted in (6.0, 10.0, 14.0, 21.0, 28.0, 35.0, 45.0, 60.0):
            sides = sim_centre.centred({home: sim_game._side(belief, home, away, set(), {}),
                                        away: sim_game._side(belief, away, home, set(), {})}, {home: wanted, away: 20.0})
            means = {t: sim_game._side_means(s) for t, s in sides.items()}
            before = (sim_game._mean_points(means[home], means[away]), sim_game._mean_points(means[away], means[home]))
            end = sim_closing_means.endings(*before)[0]
            got = before[0] + end.window + end.overtime      # the draw before the cut + the window + overtime
            worst = max(worst, abs(got - wanted))
    assert worst < 0.05


def test_a_centred_game_averages_its_target_and_projections_follow(data, belief):
    game = next(g for g in data.schedule.games if g.week == WEEK)
    home, away = game.home_team, game.away_team
    target = {home: 31.0, away: 13.0}
    sides = sim_centre.centred({home: sim_game._side(belief, home, away, set(), {}),
                                away: sim_game._side(belief, away, home, set(), {})}, target)
    scoring = sim_world.scoring_of(data)
    plain = sim_game.expected(belief, home, away, set(), scoring)
    centred = sim_game.expected(belief, home, away, set(), scoring, target=target)
    qb_home, qb_away = sides[home].qb, sides[away].qb
    assert centred[qb_home].points > plain[qb_home].points and centred[qb_away].points < plain[qb_away].points
    drawn = np.array([[r.points[home], r.points[away]] for r in sim_draws.games(belief, home, away, scoring, 4000, 3,
                                                                               target)])
    # unbiased, overtime included: se 0.17 / 0.13, so 0.3 is 1.8 / 2.3 se (9ec0677 drew +0.33 here)
    assert drawn.mean(axis=0) == pytest.approx([31.0, 13.0], abs=0.3)


@pytest.mark.parametrize("wanted", [(17.0, 17.0), (10.0, 13.0)])
def test_close_and_low_scoring_games_get_the_window_and_overtime_their_cut_margins_give(data, belief, wanted):
    """The window and overtime add the most in close games (both sides kick, ties go to overtime), and the cut
    margin spreads less in a low-scoring game: the centring's window points and tie chance (sim_closing_means, at the
    game's own expected lead and total) must be what the real endings give over the margins the draw reaches at
    the cut, read exactly from the table, so the window's own noise is out. With one spread (11.1) for every game
    the window was 0.11 a side too low at 17-17 (17 se of this test) and more at 10-13; se here 0.005-0.008 a
    side, so 0.03 is about 4 se. The tie chance is a normal spread's, and real cut margins are lumpier (3s and 7s):
    it runs 9% under the drawn one at 17-17 and 24% under at 10-13 (0.01-0.04 overtime points a side); a fixed
    4.2% for every game was 33% / 46% under, so 30% catches that."""
    from services import sim_closing_means as cm
    from services.sim_closing import points_of, stored
    game = next(g for g in data.schedule.games if g.week == WEEK)
    home, away = game.home_team, game.away_team
    sides = sim_draws.centred_sides(belief, home, away, {home: wanted[0], away: wanted[1]})
    means = [sim_game._side_means(s) for s in sides]
    ends = cm.endings(sim_game._mean_points(means[0], means[1]), sim_game._mean_points(means[1], means[0]))
    rng, scoring = np.random.default_rng(5), sim_world.scoring_of(data)
    margins = np.array([np.subtract(*sim_draw.draw_game(sides, rng, scoring, until_cut=True)) for _ in range(2500)])
    rows, ties = cm._tables()
    points = np.array([points_of(dict(zip(stored()["events"], row))) for row in rows])
    index = np.clip(margins, -cm.SPAN, cm.SPAN) + cm.SPAN
    assert points[index].mean() == pytest.approx(ends[0].window, abs=0.03)        # home leads by the margin
    assert points[2 * cm.SPAN - index].mean() == pytest.approx(ends[1].window, abs=0.03)
    assert cm.tie_chance(ends[0].lead, ends[0].sd) == pytest.approx(ties[index].mean(), rel=0.3)


def test_touchdowns_are_drive_limited_and_never_outnumber_the_plays():
    rng = np.random.default_rng(1)
    catches, carries = np.array([6, 4, 2, 0]), np.array([15, 3, 0])
    rec_rate, rush_rate = np.array([0.1, 0.1, 0.1, 0.5]), np.array([0.05, 0.05, 0.3])
    totals = []
    for _ in range(3000):
        rec, rush = sim_draw._touchdowns(catches, rec_rate, carries, rush_rate, 11, rng)
        assert (rec <= catches).all() and (rush <= carries).all()
        totals.append(rec.sum() + rush.sum())
    mean = float((catches * rec_rate).sum() + (carries * rush_rate).sum())
    assert np.mean(totals) == pytest.approx(mean, abs=0.05)
    assert np.var(totals) < mean                      # less dispersed than independent plays (Poisson)


def test_a_tied_game_goes_to_overtime_and_the_box_score_still_adds_up(data, belief, monkeypatch):
    monkeypatch.setattr(sim_draw, "ot_tie", lambda: 0.0)
    game = next(g for g in data.schedule.games if g.week == WEEK)
    home, away = game.home_team, game.away_team
    scoring = sim_world.scoring_of(data)
    seen = 0
    real_ot = sim_draw._overtime

    def counted(*args):
        nonlocal seen
        seen += 1
        before = list(args[3])
        offense, scored, points, targets, carries = real_ot(*args)
        winner = int(points[1] > points[0])
        added = points[winner] - before[winner]
        assert added in (3, 6) and points[1 - winner] == before[1 - winner]
        tds = offense[winner].tds - args[1][winner].tds
        assert (added, tds) in ((3, 0), (6, 1))           # a TD is on a player's line, a field goal on the kick
        return offense, scored, points, targets, carries

    monkeypatch.setattr(sim_draw, "_overtime", counted)
    for seed in range(400):
        result = sim_game.play(belief, home, away, set(), np.random.default_rng(seed), scoring)
        assert result.points[home] != result.points[away]
    assert seen > 0


def test_expert_opinion_persists_from_week_to_week():
    ids = list(range(1, 2001))
    first = sim_market.opinion({}, ids, np.random.default_rng(1))
    second = sim_market.opinion(first, ids, np.random.default_rng(2))
    a, b = np.array([first[i] for i in ids]), np.array([second[i] for i in ids])
    assert np.corrcoef(a, b)[0, 1] == pytest.approx(sim_market.opinion_knob("opinion_keep"), abs=0.05)
    assert b.std() == pytest.approx(1.0, abs=0.06)


@pytest.fixture(scope="module")
def simulated(data):
    return sim_season.simulate(data, SEED, WEEK)


def test_a_simulated_season_stores_its_lines_and_scores_follow_them(data, simulated):
    played = [g for g in simulated.schedule.games if g.week >= WEEK]
    assert played and all(g.spread_line is not None and g.total_line is not None for g in played)
    assert [g for g in simulated.schedule.games if g.week < WEEK] == [g for g in data.schedule.games if g.week < WEEK]
    stored = replay_blob.loads(replay_blob.dumps(simulated))
    assert stored.schedule == simulated.schedule


def test_experts_revise_a_healthy_players_value_slowly_and_an_injured_ones_at_once(belief):
    healthy = next(pid for pid, p in belief.players.items() if p.out_weeks == 0)
    hurt = replace(belief, players={**belief.players, healthy: replace(belief.players[healthy], out_weeks=2)})
    step = sim_market.opinion_knob("worth_step")
    assert sim_market.held({healthy: 10.0}, {healthy: 20.0}, belief)[healthy] == pytest.approx(10 + step * 10)
    assert sim_market.held({healthy: 10.0}, {healthy: 2.0}, hurt)[healthy] == 2.0
    assert sim_market.held({}, {healthy: 20.0}, belief)[healthy] == 20.0


def test_a_team_that_has_been_scoring_gets_a_scoring_edge_in_the_experts_values(data, belief):
    known = data.as_of(WEEK)
    edges = sim_lines.edges(known, WEEK, belief)
    rated = sim_lines.ratings(sim_lines.scores(known, WEEK), WEEK)
    best = max(rated.teams, key=lambda t: rated.teams[t].offense)
    worst = min(rated.teams, key=lambda t: rated.teams[t].offense)
    assert edges[best] - edges[worst] > 0
    scoring = sim_world.scoring_of(data)
    plain, edged = sim_market.values(belief, scoring), sim_market.values(belief, scoring, edges=edges)
    qb = next(pid for pid, p in belief.players.items() if p.team == best and p.position == "QB" and p.starts > 0.5)
    assert (edged[qb] > plain[qb]) == (edges[best] > 0)


def test_experts_take_injury_news_in_full_for_the_player_and_his_position_group(belief):
    team = sorted(belief.teams)[0]
    rbs = sorted((p for p in belief.players.values() if p.team == team and p.position == "RB"),
                 key=lambda p: -p.carries)
    starter, backup = rbs[0].player_id, rbs[1].player_id
    other = next(pid for pid, p in belief.players.items() if p.team != team and p.out_weeks == 0)
    hurt = replace(belief, players={**belief.players, starter: replace(belief.players[starter], out_weeks=4)})
    before, now = {backup: 5.0, other: 5.0, starter: 15.0}, {backup: 12.0, other: 9.0, starter: 1.0}
    held = sim_market.held(before, now, hurt, was_out=())
    assert held[backup] == 12.0 and held[starter] == 1.0          # the starter went on IR: news for the group
    assert held[other] == pytest.approx(5.0 + sim_market.opinion_knob("worth_step") * 4.0)
    back = sim_market.held({starter: 1.0}, {starter: 15.0}, belief, was_out={starter})
    assert back[starter] == 15.0                                   # a star back from injury: at once


def test_a_player_who_left_hurt_never_scores_the_overtime_touchdown_and_attempts_keep_up(data, belief, monkeypatch):
    monkeypatch.setattr(sim_draw, "ot_tie", lambda: 0.0)
    monkeypatch.setattr(sim_draw, "ot_field_goal", lambda: 0.0)
    game = next(g for g in data.schedule.games if g.week == WEEK)
    home, away = game.home_team, game.away_team
    star = max((p for p in belief.players.values() if p.team == home and p.position in ("RB", "WR", "TE")),
               key=lambda p: p.targets + p.carries).player_id
    sides = (sim_game._side(belief, home, away, set(), {star: 0.05}), sim_game._side(belief, away, home, set(), {}))
    assert star in sides[0].exited
    means = [sim_game._side_means(s) for s in sides]
    rng = np.random.default_rng(2)
    offense = [sim_draws.offense(s, 33, 27, rng) for s in sides]
    home_wins = 0
    for _ in range(300):
        after, _, points, _, _ = sim_draw._overtime(sides, offense, [(20, {}), (20, {})], [20, 20],
                                                    np.array([33, 33]), np.array([27, 27]), rng, means, [20.0, 20.0])
        if points[0] > points[1]:
            home_wins += 1
            o, before = after[0], offense[0]
            for ids, gained in ((sides[0].receivers, o.rec_td - before.rec_td), (sides[0].rushers, o.rush_td - before.rush_td)):
                assert all(n == 0 for pid, n in zip(ids, gained) if pid == star)
            assert o.attempts - before.attempts == int((o.catches - before.catches).sum())
    assert home_wins > 0                                   # the checks above ran


def test_a_nan_target_is_refused_not_floored(data, belief):
    game = next(g for g in data.schedule.games if g.week == WEEK)
    home, away = game.home_team, game.away_team
    with pytest.raises(ValueError, match="not a number"):
        sim_centre.centred({home: sim_game._side(belief, home, away, set(), {}),
                            away: sim_game._side(belief, away, home, set(), {})}, {home: float("nan"), away: 20.0})


@pytest.mark.parametrize("week", [2, 16])
def test_the_calendar_moves_the_mix_not_a_shifted_games_points(data, belief, week):
    """A game centred on its own mean + a shift (how the truth's games are centred on the line) plays the week's pass /
    run mix at the week-free mean points: on 36ae0dc the shift was taken after the week, so week 2 landed up to 0.07
    above and week 16 0.09 below (fake season, worst side); now within 0.005."""
    worst = 0.0
    for game in (g for g in data.schedule.games if g.week == WEEK):
        home, away = game.home_team, game.away_team
        raw = {home: sim_game._side(belief, home, away, set(), {}), away: sim_game._side(belief, away, home, set(), {})}
        m = {t: sim_game._side_means(s) for t, s in raw.items()}
        free = (sim_game._mean_points(m[home], m[away]), sim_game._mean_points(m[away], m[home]))
        sides = sim_centre.centred(raw, shift={home: 0.0, away: 0.0}, week=week)
        c = [sim_game._side_means(sides[t]) for t in (home, away)]
        before = (sim_game._mean_points(c[0], c[1]), sim_game._mean_points(c[1], c[0]))
        ends = sim_closing_means.endings(*before)
        worst = max(worst, *(abs(before[i] + ends[i].window + ends[i].overtime - free[i]) for i in range(2)))
    assert worst < 0.05
