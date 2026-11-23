"""sim_season: a simulated rest-of-season (and whole season) that the Replay engine plays like a real one."""
from collections import defaultdict
from dataclasses import replace
from types import MappingProxyType

import pytest

from services import replay_game as rg, replay_pool, replay_state, sim_season, sim_world
from tests import replay_fakes as fakes

FROM = 8
SEED = 11


@pytest.fixture(scope="module")
def data():
    return fakes.fake_season()


@pytest.fixture(scope="module")
def simulated(data):
    return sim_season.simulate(data, SEED, FROM)


def _rows(season, week_test):
    return {name: {k: v for k, v in getattr(season, name).items() if week_test(k[1])}
            for name in ("actual", "projected", "box", "usage", "injuries", "expert_ranks", "notes")}


def test_real_weeks_are_copied_through_untouched(data, simulated):
    assert _rows(simulated, lambda w: w < FROM) == _rows(data, lambda w: w < FROM)
    def real(season):
        return {pid: before for pid, games in season.team_by_week.items()
                if (before := tuple(g for g in games if g[0] < FROM))}
    assert real(simulated) == real(data)
    assert simulated.players == data.players and simulated.schedule == data.schedule


def test_every_simulated_week_is_filled(data, simulated):
    for week in range(FROM, data.last_week + 1):
        playing = {t for g in data.schedule.games if g.week == week for t in (g.home_team, g.away_team)}
        dst = {pid for (pid, w) in simulated.actual if w == week and simulated.players[pid].position == "DST"}
        assert len(dst) == len(playing)
        assert any(w == week for _, w in simulated.projected)
        assert any(w == week for _, w in simulated.expert_ranks) and week in simulated.expert_age
        skill = [k for k in simulated.actual if k[1] == week and simulated.players[k[0]].position in replay_pool.SKILL]
        assert skill and all(k in simulated.box and k in simulated.usage for k in skill)
        assert all(float(v).is_integer() for k, v in simulated.actual.items() if k[1] == week
                   and simulated.players[k[0]].position in ("K", "DST"))
    teams = {(pid, w): t for pid, games in simulated.team_by_week.items() for w, t in games}
    assert all(key in teams for key in simulated.actual if key[1] >= FROM)


def test_box_lines_are_whole_numbers_and_points_match_them(simulated):
    from services.sim_game import _skill_points
    half = sim_world.scoring_of(simulated)
    for key, line in simulated.box.items():
        if key[1] >= FROM:
            assert all(float(v).is_integer() for v in line.__dict__.values())
            assert simulated.actual[key] - _skill_points(line, 0, simulated.players[key[0]].position, half) <= 0.001


def test_nobody_tagged_out_plays_and_returns_match_the_tags(simulated):
    hard = [k for k, injury in simulated.injuries.items() if injury.hard and k[1] >= FROM]
    assert hard and not [k for k in hard if k in simulated.actual]
    assert all(i.tag in replay_pool.TAGS and (i.practice is None or i.practice in replay_pool.PRACTICE)
               for i in simulated.injuries.values())
    assert dict(simulated.returns) == replay_pool.hard_tag_returns(simulated.injuries, simulated.actual, simulated.last_week)


def test_injured_players_miss_games_and_new_injuries_happen(data, simulated):
    fresh = {k for k, i in simulated.injuries.items() if i.hard and k[1] > FROM and (k[0], k[1] - 1) in simulated.actual}
    assert len(fresh) >= 5                                   # hurt in a game, tagged out the next week


def test_same_seed_same_season_other_seed_other_season(data, simulated):
    again = sim_season.simulate(data, SEED, FROM)
    assert _rows(again, lambda w: True) == _rows(simulated, lambda w: True)
    assert again.team_by_week == simulated.team_by_week and again.returns == simulated.returns
    other = sim_season.simulate(data, SEED + 1, FROM)
    assert _rows(other, lambda w: w >= FROM)["actual"] != _rows(simulated, lambda w: w >= FROM)["actual"]


def test_what_the_public_sees_before_a_week_does_not_depend_on_that_week_or_later(data, monkeypatch, simulated):
    """Re-roll every game and injury from week W on: everything known before W stays the same."""
    week = FROM + 3
    original = replay_state.rng

    def rerolled(seed, *stream):
        if stream[:2] in (("sim", "game"), ("sim", "hurt")) and stream[2] >= week:
            return original(seed + 1000, *stream)
        return original(seed, *stream)
    monkeypatch.setattr(replay_state, "rng", rerolled)
    changed = sim_season.simulate(data, SEED, FROM)
    known = lambda s, w: replay_pool._as_of.__wrapped__(s, w)   # noqa: E731
    before, after = known(simulated, week), known(changed, week)
    for name in ("actual", "projected", "box", "usage", "injuries", "expert_ranks", "notes", "expert_age"):
        assert getattr(before, name) == getattr(after, name), name
    assert _rows(changed, lambda w: w >= week)["actual"] != _rows(simulated, lambda w: w >= week)["actual"]


def test_a_whole_future_season_from_a_prior(data):
    empty = MappingProxyType({})
    prior = sim_world.estimate(data, data.last_week + 1)
    future = replace(data, actual=empty, projected=empty, team_by_week=empty, injuries=empty, usage=empty, box=empty,
                     expert_ranks=empty, expert_age=empty, returns=empty, notes=empty,
                     players=MappingProxyType({p: pl for p, pl in data.players.items() if pl.adp}))
    season = sim_season.simulate(future, 3, 1, prior)
    assert {w for _, w in season.actual} == set(range(1, data.last_week + 1))
    assert any(w == 0 for _, w in season.expert_ranks) and season.expert_age[0] == 6
    preseason = [k for k, i in season.injuries.items() if k[1] == 1 and i.hard]
    assert all(k not in season.actual for k in preseason)


def test_the_replay_engine_plays_a_simulated_season_to_a_champion(simulated):
    settings = fakes.fake_settings()
    state = rg.auto_draft(rg.create_game(settings, simulated, 5), settings, simulated, 5)
    while state.phase != "complete":
        step = rg.WEDNESDAY if state.phase == "waivers" else rg.PLAY
        state = rg.advance(state, settings, simulated, 5, step)
    assert state.champion in {team.id for team in state.teams}
    assert len(state.results) == settings.end_week
    scores = defaultdict(float)
    for result in state.results:
        for team, score in result.scores.items():
            scores[team] += score
    assert all(score > 0 for score in scores.values())
