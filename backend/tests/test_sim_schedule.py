"""sim_schedule: a 2027 schedule that follows the NFL formula (no database)."""
from collections import Counter
from types import MappingProxyType

import pytest

from services import sim_schedule as ss
from services.replay_pool import PoolPlayer, SeasonData
from services.schedule_strength import Game

STANDINGS = {team: place for teams in ss.DIVISIONS.values() for place, team in enumerate(teams, start=1)}


@pytest.fixture(scope="module")
def schedule():
    return ss.build(2027, STANDINGS, seed=3)


def _games_of(schedule, team):
    return [g for g in schedule.games if team in (g.home_team, g.away_team)]


def _other(game, team):
    return game.away_team if game.home_team == team else game.home_team


def test_every_team_17_games_8_or_9_home_one_bye(schedule):
    assert len(schedule.games) == 272
    byes = Counter()
    for team in ss.TEAMS:
        games = _games_of(schedule, team)
        assert len(games) == 17
        assert sum(g.home_team == team for g in games) == (9 if ss.DIVISION_OF[team].startswith("AFC") else 8)
        weeks = [g.week for g in games]
        assert len(set(weeks)) == 17                                  # never twice in a week
        (bye,) = set(range(1, 19)) - set(weeks)
        assert bye in ss.BYE_WEEKS
        byes[bye] += 1
    assert all(n % 2 == 0 and n <= ss.MAX_BYES for n in byes.values())


def test_opponents_follow_the_formula(schedule):
    rot = ss.rotation(2027)
    for team in ss.TEAMS:
        mine = ss.DIVISION_OF[team]
        opponents = Counter(_other(g, team) for g in _games_of(schedule, team))
        divisions = Counter(ss.DIVISION_OF[o] for o in opponents.elements())
        assert all(opponents[o] == 2 for o in ss.DIVISIONS[mine] if o != team)
        assert divisions[mine] == 6 and divisions[rot["intra"][mine]] == 4 and divisions[rot["inter"][mine]] == 4
        place = STANDINGS[team]
        same_conf = [d for d in ss.DIVISIONS if d[:3] == mine[:3] and d not in (mine, rot["intra"][mine])]
        for division in (*same_conf, rot["17th"][mine]):
            assert divisions[division] == 1
            (rival,) = [o for o in opponents if ss.DIVISION_OF[o] == division]
            assert STANDINGS[rival] == place


def test_2027_rotation_matches_published_pairings():
    rot = ss.rotation(2027)
    assert rot["intra"]["NFC South"] == "NFC East" and rot["inter"]["NFC South"] == "AFC South"
    assert rot["17th"]["NFC South"] == "AFC East"                    # Falcons' 2027 opponents page
    assert rot["intra"]["AFC East"] == "AFC South" and rot["inter"]["AFC East"] == "NFC East"


def test_week_18_is_divisional_and_dates_are_sundays(schedule):
    assert all(ss.DIVISION_OF[g.home_team] == ss.DIVISION_OF[g.away_team] for g in schedule.games if g.week == 18)
    week_one = {g.gameday for g in schedule.games if g.week == 1}
    assert week_one == {"2027-09-12"}
    assert all(g.home_score is None and g.away_score is None and g.gametime for g in schedule.games)
    assert all(g.kickoff is not None for g in schedule.games)


def test_same_seed_same_schedule_other_seed_differs(schedule):
    assert ss.build(2027, STANDINGS, seed=3).games == schedule.games
    assert ss.build(2027, STANDINGS, seed=4).games != schedule.games


def test_bad_standings_rejected():
    with pytest.raises(ValueError):
        ss.build(2027, {**STANDINGS, "BUF": 2}, seed=1)


def test_works_with_season_data(schedule):
    game = next(g for g in schedule.games if g.week == 1)
    bye = next(w for w in ss.BYE_WEEKS if schedule.opponent(game.home_team, w) is None)
    data = SeasonData(season=2027, scoring="half", players=MappingProxyType({1: PoolPlayer(1, "A", "WR")}),
                      schedule=schedule, actual={}, projected={},
                      team_by_week=MappingProxyType({1: ((1, game.home_team),)}), last_week=18)
    assert schedule.opponent(game.home_team, 1) == game.away_team
    assert data.on_bye(1, bye) and not data.on_bye(1, 1)


def test_standings_now_with_tiebreakers():
    def final(week, home, away, hs, as_):
        return Game(week, home, away, hs, as_, None, None)
    games = [final(1, "BUF", "MIA", 20, 10), final(2, "MIA", "BUF", 30, 0), final(3, "NE", "NYJ", 10, 7),
             final(4, "BUF", "NE", 3, 0), final(5, "MIA", "NYJ", 7, 3), final(6, "NYJ", "NE", 17, 14)]
    places = ss.standings_now(games)
    # BUF and MIA 2-1 (split head-to-head; division record equal; MIA better differential)
    assert places["MIA"] == 1 and places["BUF"] == 2
    assert {places["NE"], places["NYJ"]} == {3, 4}
    assert ss.standings_now(games, before_week=2)["BUF"] == 1


def hash_seed_outputs(code: str, seeds=(1, 2, 3)) -> list[str]:
    """`code`'s stdout in fresh interpreters under different PYTHONHASHSEEDs (set and str order differ)."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    outputs = []
    for seed in seeds:
        env = {**os.environ, "PYTHONHASHSEED": str(seed), "PYTHONPATH": str(backend), "FANTASY_ENV_FILE": ""}
        run = subprocess.run([sys.executable, "-c", code], cwd=backend, env=env, capture_output=True, text=True,
                             timeout=600)
        assert run.returncode == 0, run.stderr
        outputs.append(run.stdout)
    return outputs


def test_the_schedule_is_the_same_in_every_process():
    """Every league of a season shares one schedule, so it can't depend on str hash order (per process)."""
    code = ("import hashlib\nfrom services import sim_schedule as ss\n"
            "st = {t: p for teams in ss.DIVISIONS.values() for p, t in enumerate(teams, start=1)}\n"
            "games = ss.build(2027, st, 7).games\n"
            "print(hashlib.md5(repr([(g.week, g.home_team, g.away_team, g.gametime) for g in games]).encode()).hexdigest())")
    outputs = hash_seed_outputs(code)
    assert len(set(outputs)) == 1, outputs
