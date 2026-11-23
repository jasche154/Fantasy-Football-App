"""Replay League playoff bracket view (services/replay_views_playoffs.py): every round shown before it is
known, round one projected from the current seeds, byes, the consolation pools, and the champion."""
from dataclasses import replace
from functools import lru_cache

from services import replay_game as rg, replay_state as rs, replay_views as views, replay_views_league as league
from tests import replay_fakes as fakes
from tests.test_replay_views import DATA, SEED, SETTINGS, at, finished, game

SIX = replace(SETTINGS, playoff_teams=6)          # a three-round bracket: weeks 15-17, two byes
SEED_KEYS = {"team_id", "name", "is_user", "tendency", "seed"}
SIDE_KEYS = SEED_KEYS | {"points", "projected"}


@lru_cache(maxsize=None)
def six_at(week: int) -> rs.State:
    """The six-team-playoff league just before `week` is played (week > end_week: the season is over)."""
    if week == 1:
        return rg.auto_draft(rg.create_game(SIX, DATA, SEED), SIX, DATA, SEED)
    played = rg.advance(six_at(week - 1), SIX, DATA, SEED, "play")
    return played if week > SIX.end_week else rg.advance(played, SIX, DATA, SEED, "wednesday")


def six(state: rs.State) -> views.Game:
    return views.Game(1, "Six", SIX, state, DATA, "2026-01-01T00:00:00", SEED)


def test_before_the_playoffs_round_one_is_projected_from_todays_seeds_and_later_rounds_are_open():
    bracket = league.playoffs(game(at(4)))

    assert (bracket["weeks"], bracket["seeds_final"], bracket["champion"]) == ([15, 16], False, None)
    semis, final = bracket["title"]
    assert (semis["name"], semis["projected"], semis["byes"]) == ("Semi-finals", True, [])
    assert [(g["home"]["seed"], g["away"]["seed"]) for g in semis["games"]] == [(1, 4), (2, 3)]
    assert set(semis["games"][0]["home"]) == SIDE_KEYS and semis["games"][0]["home"]["points"] is None
    assert (final["name"], final["projected"]) == ("Final", False)
    assert [(g["label"], g["home"], g["away"]) for g in final["games"]] == [("Championship", None, None), ("Third place", None, None)]
    pool, = bracket["pools"]
    assert (pool["places"], pool["size"], [t["seed"] for t in pool["teams"]]) == ([5, 12], 8, list(range(5, 13)))
    assert [(w["week"], w["known"], w["games"]) for w in pool["weeks"]] == [(15, False, []), (16, False, [])]


def test_a_finished_season_fills_every_slot_and_names_the_champion():
    bracket = league.playoffs(game(finished()))
    semis, final = bracket["title"]

    assert not semis["projected"] and all(g["winner"] in (g["home"]["team_id"], g["away"]["team_id"]) for g in semis["games"])
    winners = {g["winner"] for g in semis["games"]}
    championship = final["games"][0]
    assert {championship["home"]["team_id"], championship["away"]["team_id"]} == winners
    assert bracket["champion"]["team_id"] == championship["winner"] == finished().champion
    pool, = bracket["pools"]
    assert all(w["known"] and len(w["games"]) == 4 for w in pool["weeks"])
    inside = {t["team_id"] for t in pool["teams"]}
    assert all({g["home"]["team_id"], g["away"]["team_id"]} <= inside for w in pool["weeks"] for g in w["games"])


def test_a_three_round_bracket_has_byes_a_losers_pool_and_tbd_slots_until_decided():
    start = league.playoffs(six(six_at(15)))               # seeds final, round one set, nothing played yet
    quarters, semis, final = start["title"]

    assert [r["week"] for r in start["title"]] == [15, 16, 17] and start["seeds_final"]
    assert [b["seed"] for b in quarters["byes"]] == [1, 2] and set(quarters["byes"][0]) == SEED_KEYS
    assert [(g["home"]["seed"], g["away"]["seed"]) for g in quarters["games"]] == [(3, 6), (4, 5)]
    assert [g["home"] for g in semis["games"]] == [None, None]
    losers, rest = start["pools"]
    assert (losers["places"], losers["size"], losers["teams"], losers["first_week"]) == ([5, 6], 2, None, 16)
    assert (rest["places"], [t["seed"] for t in rest["teams"]]) == ([7, 12], list(range(7, 13)))
    assert rest["weeks"][0]["known"] and len(rest["weeks"][0]["games"]) == 3

    later = league.playoffs(six(six_at(16)))               # round one played
    losers = later["pools"][0]
    quarter_losers = {next(t for t in (g["home"], g["away"]) if t["team_id"] != g["winner"])["team_id"]
                      for g in later["title"][0]["games"]}
    assert {t["team_id"] for t in losers["teams"]} == quarter_losers
    assert all(g["home"] is not None for g in later["title"][1]["games"])
    assert {b["seed"] for b in later["title"][0]["byes"]} <= {g["home"]["seed"] for g in later["title"][1]["games"]}


def test_the_bracket_never_shows_a_week_not_played_yet():
    state = at(15)
    assert league.playoffs(game(state)) == league.playoffs(game(state, fakes.future_changed(DATA, state.week)))
