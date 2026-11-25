"""sim_lines: posted lines, the no-hindsight proofs for the pregame line, last season's ratings, old saves."""
from dataclasses import replace
from pathlib import Path

import pytest

from database.models import NflGame
from services import replay_blob, sim_lines, sim_season, sim_setup, sim_talent, sim_world
from services.sim_world import RoleChange
from tests import replay_fakes as fakes

WEEK = 9
SEED = 5
OLD_BLOB = Path(__file__).with_name("fixtures") / "sim_season_2ffb5f3.blob"   # written by the code at 2ffb5f3


@pytest.fixture(scope="module")
def data():
    return fakes.fake_season()


@pytest.fixture(scope="module")
def lined(data):
    """The season with a pregame line on every game, as a real season has its closing lines."""
    return replace(data, schedule=replace(data.schedule, games=tuple(
        replace(g, spread_line=float(g.week % 7) - 3.0, total_line=40.0 + g.week) for g in data.schedule.games)))


def _games(data, week):
    return [g for g in data.schedule.games if g.week == week]


def _lines(data, weeks):
    return {(g.home_team, g.week): (g.spread_line, g.total_line) for g in data.schedule.games if g.week in weeks}


def test_as_of_keeps_the_lines_through_its_week_only(lined):
    known = lined.as_of(WEEK)
    assert all(g.spread_line is not None for g in known.schedule.games if g.week <= WEEK)
    assert all(g.spread_line is None and g.total_line is None for g in known.schedule.games if g.week > WEEK)
    assert known.schedule.opponents == lined.schedule.opponents
    changed = fakes.future_changed(lined, WEEK)                    # later lines (and everything else) differ
    assert changed.schedule != lined.schedule and changed.as_of(WEEK).schedule == known.schedule


def test_posted_lines_are_the_first_simulated_weeks_only(lined):
    posted = sim_setup.posted_lines(lined, WEEK)
    assert set(posted) == {(g.home_team, WEEK) for g in _games(lined, WEEK)}
    half = replace(lined, schedule=replace(lined.schedule, games=tuple(
        replace(g, spread_line=None) if g.week == WEEK else g for g in lined.schedule.games)))
    assert all(spread is None and total is not None for spread, total in sim_setup.posted_lines(half, WEEK).values())


def test_a_posted_line_is_used_in_the_first_simulated_week_only(data):
    first, second = _games(data, WEEK)[0], _games(data, WEEK + 1)[0]
    posted = {(first.home_team, WEEK): (-6.5, 41.5), (second.home_team, WEEK + 1): (-20.5, 70.0)}
    season = sim_season.simulate(data, SEED, WEEK, posted=posted)
    lines = _lines(season, {WEEK, WEEK + 1})
    assert lines[(first.home_team, WEEK)] == (-6.5, 41.5)
    assert lines[(second.home_team, WEEK + 1)] != (-20.5, 70.0)     # a later week's posted line knows nothing of the sim


def test_a_partial_or_absurd_posted_line_falls_back_to_the_model_part_by_part(data):
    belief = sim_world.estimate(data, WEEK)
    home = _games(data, WEEK)[0].home_team
    model = sim_lines.week_lines(data.as_of(WEEK), WEEK, belief, set())[home]

    def line(posted):
        return sim_lines.week_lines(data.as_of(WEEK), WEEK, belief, set(), posted={(home, WEEK): posted})[home]

    assert line((None, 41.5)) == sim_lines.Line(model.margin, 41.5)
    assert line((-6.5, None)) == sim_lines.Line(-6.5, model.total)
    assert line((99.0, 41.5)) == sim_lines.Line(model.margin, 41.5)            # logged and ignored
    assert line((-6.5, float("nan"))) == sim_lines.Line(-6.5, model.total)
    assert line((-6.5, 3.0)) == sim_lines.Line(-6.5, model.total)
    assert line((-6.5, 20.0)).total >= sim_lines.MIN_TOTAL


def test_the_first_simulated_weeks_line_never_sees_the_hidden_truth(data, monkeypatch):
    """The truth (hidden team strength) changes every simulated result but not the line of the first simulated
    week, which is made before any of them; later lines move, as the public learns from the scores."""
    plain = sim_season.simulate(data, SEED, WEEK)
    monkeypatch.setattr(sim_talent, "strength", lambda teams, seed: {t: (9.0, -9.0) if i % 2 else (-9.0, 9.0)
                                                                     for i, t in enumerate(sorted(teams))})
    strong = sim_season.simulate(data, SEED, WEEK)
    assert _lines(strong, {WEEK}) == _lines(plain, {WEEK})
    assert strong.scores != plain.scores
    assert _lines(strong, {WEEK + 2}) != _lines(plain, {WEEK + 2})


def test_a_weeks_line_never_sees_the_truths_results_from_that_week_on(data, monkeypatch):
    """A role change the truth makes from week W on changes results from W on, never a line up to W."""
    later = WEEK + 3
    plain = sim_season.simulate(data, SEED, WEEK)
    truth = sim_talent.truth

    def changed(belief, seed, from_week, last_week, rookies=()):
        world = truth(belief, seed, from_week, last_week, rookies)
        team = sorted(world.teams)[0]
        stars = sorted((p for p in world.players.values() if p.team == team and p.position in ("QB", "RB", "WR")),
                       key=lambda p: -(p.targets + p.carries + p.starts))[:4]
        busts = tuple(RoleChange(later, p.player_id, 0.05, 0.05, 0.05, "bust") for p in stars)
        return replace(world, changes=tuple(sorted((*world.changes, *busts), key=lambda c: (c.week, c.player_id))))

    monkeypatch.setattr(sim_talent, "truth", changed)
    busted = sim_season.simulate(data, SEED, WEEK)
    upto = set(range(WEEK, later + 1))
    assert _lines(busted, upto) == _lines(plain, upto)
    assert {k: v for k, v in busted.scores.items() if k[1] >= later} != {k: v for k, v in plain.scores.items()
                                                                         if k[1] >= later}


def test_last_seasons_ratings_come_from_its_real_scores(session):
    games = [NflGame(game_id=f"2030_{w:02d}_A_B", season=2030, week=w, game_type="REG", gameday=None,
                     home_team="AAA", away_team="BBB", home_score=38, away_score=10) for w in range(1, 10)]
    games.append(NflGame(game_id="2030_10_A_B", season=2030, week=10, game_type="REG", gameday=None,
                         home_team="AAA", away_team="BBB", home_score=None, away_score=None))
    session.add_all(games)
    session.flush()
    prior = sim_setup.team_prior(session, 2031)
    assert prior["AAA"].offense > 2 and prior["BBB"].defense > 2 and prior["BBB"].offense < 0
    assert sim_setup.team_prior(session, 2040) == {}              # no scores for the season before: no prior


def test_a_season_saved_before_the_lines_still_loads_and_plays():
    old = replay_blob.loads(OLD_BLOB.read_bytes())
    assert old.scores and all(g.spread_line is None for g in old.schedule.games)
    assert old.as_of(old.last_week).schedule.opponents == old.schedule.opponents
    sim_world.estimate(old, old.last_week)                         # the belief reads it as before
