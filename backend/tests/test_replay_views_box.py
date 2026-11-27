"""Replay League box score: one played NFL game, both teams' players and the final score. No hindsight."""
from dataclasses import asdict, replace
from types import MappingProxyType

import pytest

from services import replay_views_league as views
from tests.test_replay_views import DATA, at, game, hidden_changed

BOX_TEAM = {"team", "home", "score", "players"}
BOX_PLAYER = {"id", "name", "position", "points", "box"}


def _a_game(week: int):
    return next(g for g in DATA.schedule.games if g.week == week)


def test_a_played_game_lists_both_teams_every_player_and_the_final_score():
    played = _a_game(3)

    shown = views.box_score(game(at(6)), 3, played.home_team)

    assert shown["week"] == 3 and [side["team"] for side in shown["teams"]] == [played.away_team, played.home_team]
    for side in shown["teams"]:
        assert set(side) == BOX_TEAM and side["home"] == (side["team"] == played.home_team)
        assert side["score"] == DATA.scores[(side["team"], 3)]
        expected = {pid for pid, games in DATA.team_by_week.items() if (3, side["team"]) in games}
        assert {row["id"] for row in side["players"]} == expected
        for row in side["players"]:
            assert set(row) == BOX_PLAYER and row["points"] == DATA.actual[(row["id"], 3)]
            box = DATA.box.get((row["id"], 3))
            assert row["box"] == (asdict(box) if box else None)
            assert (row["box"] is None) == (row["position"] in ("K", "DST"))
        order = [("QB", "RB", "WR", "TE", "K", "DST").index(row["position"]) for row in side["players"]]
        assert order == sorted(order) and {"K", "DST"} <= {row["position"] for row in side["players"]}
    assert views.box_score(game(at(6)), 3, played.away_team) == shown          # either team names the game


def test_only_played_weeks_and_real_games_have_a_box_score():
    league = game(at(6))
    team = _a_game(4).home_team
    bye_team = next(team for team in {g.home_team for g in DATA.schedule.games}
                    if DATA.schedule.opponent(team, 6) is None)

    for week in (0, 6, 7, 18):
        with pytest.raises(ValueError, match="not been played"):
            views.box_score(league, week, team)
    with pytest.raises(ValueError, match="no game"):
        views.box_score(game(at(7)), 6, bye_team)
    with pytest.raises(ValueError, match="no game"):
        views.box_score(league, 3, "XYZ")


def test_a_box_score_never_shows_a_week_not_played_yet():
    week = 6
    league, changed = game(at(week)), game(at(week), hidden_changed(week))
    assert max(w for _, w in league.data.scores) == week - 1          # results of played weeks only
    for shown_week in range(1, week):
        for played in (g for g in DATA.schedule.games if g.week == shown_week):
            assert views.box_score(league, shown_week, played.home_team) == \
                views.box_score(changed, shown_week, played.home_team)


def test_a_season_without_scores_shows_none():
    played = _a_game(2)
    old = replace(DATA, scores=MappingProxyType({}))      # a league saved before final scores were kept

    shown = views.box_score(game(at(3), old), 2, played.home_team)

    assert [side["score"] for side in shown["teams"]] == [None, None] and shown["teams"][0]["players"]


def test_a_trade_value_rounds_half_up_like_the_trade_screen():
    """Lists send whole values; the trade screen rounds the chart number in the browser (half up): one answer.
    Python's round() rounds half to even (512.5 -> 512), so a player could show 512 on his row and 513 in a deal."""
    from services import replay_views

    assert [replay_views.whole(x) for x in (512.5, 511.5, 0.5, 512.4, 1000.0, 0.0)] == [513, 512, 1, 512, 1000, 0]


def test_a_passing_line_saved_without_attempts_shows_no_completions_or_attempts():
    played = _a_game(2)
    old = replace(DATA, box=MappingProxyType({key: replace(box, pass_cmp=0, pass_att=0, pass_yds=200 if DATA.players[key[0]].position == "QB" else 0)
                                              for key, box in DATA.box.items()}))

    rows = [row for side in views.box_score(game(at(3), old), 2, played.home_team)["teams"] for row in side["players"]]

    passers = [row["box"] for row in rows if row["box"] and row["box"]["pass_yds"]]
    others = [row["box"] for row in rows if row["box"] and not row["box"]["pass_yds"]]
    assert passers and all(box["pass_cmp"] is None and box["pass_att"] is None for box in passers)
    assert others and all(box["pass_att"] == 0 for box in others if not (box["pass_td"] or box["pass_int"]))
