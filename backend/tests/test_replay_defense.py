"""Replay League defense ratings: point in time, opponent-adjusted, blended with last season, tiered by quarter."""
from collections.abc import Callable
from dataclasses import replace
from types import MappingProxyType

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from database.models import Base
from services import replay_defense as rd
from services.replay_pool import PoolPlayer, SeasonData
from services.schedule_strength import Game, Schedule
from tests import replay_fakes as fakes

DATA = fakes.fake_season()
RATING = {"rank", "of", "tier", "factor", "allowed"}


def tiny_season(games: dict[int, list[tuple[str, str]]], points: Callable[[str, str], float]) -> SeasonData:
    """One RB per team, scoring points(his team, the defense) in every game."""
    teams = sorted({team for pairs in games.values() for pair in pairs for team in pair})
    ids = {team: n for n, team in enumerate(teams, start=1)}
    opponents, listed = {}, []
    for week, pairs in games.items():
        for home, away in pairs:
            opponents |= {(home, week): away, (away, week): home}
            listed.append(Game(week, home, away, None, None, None, None))
    actual = {(ids[team], week): points(team, defense) for (team, week), defense in opponents.items()}
    team_by_week = {ids[team]: tuple((week, team) for week in games if (team, week) in opponents) for team in teams}
    return SeasonData(season=2021, scoring="half",
                      players=MappingProxyType({n: PoolPlayer(n, f"Back {team}", "RB") for team, n in ids.items()}),
                      schedule=Schedule(tuple(listed), MappingProxyType(opponents)),
                      actual=MappingProxyType(actual), projected=MappingProxyType({}),
                      team_by_week=MappingProxyType(team_by_week), last_week=max(games))


def test_a_defense_that_met_the_strong_offense_twice_is_not_rated_generous():
    uneven = {1: [("A", "B"), ("C", "D")], 2: [("A", "C"), ("B", "D")], 3: [("A", "B"), ("C", "D")]}
    ratings = rd.ratings(tiny_season(uneven, lambda offense, _: 30.0 if offense == "A" else 10.0))

    b, d = ratings.rating("B", "RB"), ratings.rating("D", "RB")

    assert set(b) == RATING and b["of"] == 4                    # raw, B gave up 23.3 a game and D 10
    assert b["factor"] == pytest.approx(1.0, abs=0.01) and d["factor"] == pytest.approx(1.0, abs=0.01)
    assert b["allowed"] == pytest.approx(15.0, abs=0.1)         # the league's 15 a game x factor 1


def test_a_generous_defense_ranks_first_and_easy_and_a_stingy_one_last_and_tough():
    weeks = {1: [("A", "B"), ("C", "D")], 2: [("A", "C"), ("B", "D")], 3: [("A", "D"), ("B", "C")]}
    allowed = {"A": 20.0, "B": 12.0, "C": 10.0, "D": 6.0}
    ratings = rd.ratings(tiny_season(weeks, lambda _, defense: allowed[defense]))

    shown = {team: ratings.rating(team, "RB") for team in "ABCD"}

    assert [shown[team]["rank"] for team in "ABCD"] == [1, 2, 3, 4]
    assert [shown[team]["tier"] for team in "ABCD"] == ["easy", "neutral", "neutral", "tough"]
    assert shown["A"]["factor"] > 1.1 > 0.9 > shown["D"]["factor"]


def test_nothing_known_before_week_one_without_last_season_and_k_dst_and_byes_have_none():
    week_one, later = rd.ratings(DATA.as_of(1)), rd.ratings(DATA.as_of(6))

    assert all(week_one.rating(team, "RB") is None for team in fakes.TEAMS)
    assert later.rating(fakes.TEAMS[0], "K") is None and later.rating(fakes.TEAMS[0], "DST") is None
    assert later.rating(None, "RB") is None and later.rating("XYZ", "WR") is None
    assert later.rating(fakes.TEAMS[0], "RB") is not None


def test_last_season_gives_week_one_a_half_strength_rating_that_fades_with_games():
    prior = {(team, "WR"): 1.0 + 0.01 * n for n, team in enumerate(fakes.TEAMS)} | {(rd.AVERAGE, "WR"): 20.0}
    with_prior = replace(DATA, defense_prior=MappingProxyType(prior))
    last = fakes.TEAMS[-1]

    week_one, week_six = rd.ratings(with_prior.as_of(1)), rd.ratings(with_prior.as_of(6))

    top = week_one.rating(last, "WR")
    assert top["rank"] == 1 and top["factor"] == pytest.approx(1 + 0.31 * 0.5, abs=0.001)
    assert top["allowed"] == pytest.approx(20.0 * top["factor"], abs=0.1) and top["tier"] == "easy"
    assert week_one.rating(last, "RB") is None                  # no RB prior and no games yet
    solved = rd.ratings(DATA.as_of(6)).rating(last, "WR")["factor"]
    games = sum(1 for week in range(1, 6) if DATA.schedule.opponent(last, week))
    blended = solved + 0.31 * rd.PRIOR_WEIGHT * rd.PRIOR_GAMES / (games + rd.PRIOR_GAMES)
    assert week_six.rating(last, "WR")["factor"] == pytest.approx(blended, abs=0.002)
    assert rd.basis(with_prior.as_of(6)) == {"weeks": 5, "prior": True}
    assert rd.basis(DATA.as_of(1)) == {"weeks": 0, "prior": False}


def test_tiers_split_the_league_by_quarter():
    ratings = rd.ratings(DATA.as_of(8))

    shown = [ratings.rating(team, "RB") for team in fakes.TEAMS]

    tiers = [rating["tier"] for rating in shown]
    assert (tiers.count("easy"), tiers.count("tough"), tiers.count("neutral")) == (8, 8, 16)
    assert sorted(rating["rank"] for rating in shown) == list(range(1, 33))
    assert all(rating["tier"] == "easy" for rating in shown if rating["rank"] <= 8)


@pytest.mark.parametrize("week", [2, 6, 12])
def test_ratings_never_see_the_weeks_not_played(week):
    later = fakes.future_changed(DATA, after_week=week - 1)

    ours, theirs = rd.ratings(DATA.as_of(week)), rd.ratings(later.as_of(week))

    assert ours.table == theirs.table and ours.table
    assert rd.ratings(DATA.as_of(week + 1)).table != rd.ratings(later.as_of(week + 1)).table   # the probe is live


def test_ratings_are_cached_per_season_copy():
    assert rd.ratings(DATA.as_of(5)) is rd.ratings(DATA.as_of(5))


def test_load_prior_reads_last_season_when_it_is_loaded():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        fakes.seed_db(session, fakes.fake_season(season=fakes.SEASON - 1))
        prior = rd.load_prior(session, fakes.SEASON, "half")
        nothing = rd.load_prior(session, fakes.SEASON - 1, "half")
    engine.dispose()

    assert nothing == {}
    assert {position for _, position in prior} == {"QB", "RB", "WR", "TE"}
    assert {team for team, _ in prior} == {*fakes.TEAMS, rd.AVERAGE}
    assert all(0.7 <= factor <= 1.35 for (team, _), factor in prior.items() if team != rd.AVERAGE)
    assert prior[(rd.AVERAGE, "QB")] > prior[(rd.AVERAGE, "TE")] > 0
