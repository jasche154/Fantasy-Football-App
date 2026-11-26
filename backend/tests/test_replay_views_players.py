"""Replay League wire and player page, round 3: stat lines (season, last 4, one week), sorting by any column,
defense ratings on rows, and the player page's box scores and upcoming estimates. No hindsight throughout."""
import json
from dataclasses import asdict

import pytest

from services import replay_advanced, replay_box, replay_defense, replay_views_players as wire
from tests import replay_fakes as fakes
from tests.test_replay_views import DATA, at, drafting, finished, game, hidden_changed

STAT_LINE = {"span", "week", "games", "points", "projection", "box", "adv"}
UPCOMING = {"week", "opponent", "away", "bye", "estimate", "defense", "out_expected"}


def rows(state, **query) -> list[dict]:
    return wire.players(game(state), status="all", limit=10_000, **query)["results"]


def by_id(state, **query) -> dict[int, dict]:
    return {row["player"]["id"]: row for row in rows(state, **query)}


def played(pid: int, before: int) -> list[int]:
    return [week for week in range(1, before) if (pid, week) in DATA.actual]


# ---------------- stat lines ----------------

def test_the_season_line_totals_his_games_so_far_with_a_box_for_skill_players_only():
    shown = by_id(at(6))
    back, kicker = fakes.player_id("RB", 1), fakes.player_id("K", 1)

    line = shown[back]["line"]
    weeks = played(back, 6)
    assert set(line) == STAT_LINE and (line["span"], line["week"], line["projection"]) == ("season", None, None)
    assert line["games"] == len(weeks) and line["points"] == pytest.approx(sum(DATA.actual[(back, w)] for w in weeks))
    assert line["box"] == asdict(replay_box.total(DATA.box[(back, w)] for w in weeks))
    assert line["adv"] == replay_advanced.line([DATA.box[(back, w)] for w in weeks],
                                               [DATA.usage[(back, w)] for w in weeks if (back, w) in DATA.usage])
    assert shown[kicker]["line"]["box"] is None and shown[kicker]["line"]["games"] > 0
    assert shown[kicker]["line"]["adv"] is None
    assert shown[fakes.NEVER_PLAYED_ID]["line"] == {"span": "season", "week": None, "games": 0, "points": 0,
                                                    "projection": None, "box": None, "adv": None}


def test_the_last4_line_averages_his_last_four_games():
    shown = by_id(at(9), stats="last4")
    back = fakes.player_id("RB", 2)

    weeks = played(back, 9)[-4:]
    line = shown[back]["line"]
    assert (line["span"], line["games"]) == ("last4", 4)
    assert line["points"] == pytest.approx(sum(DATA.actual[(back, w)] for w in weeks) / 4, abs=0.01)
    assert line["box"] == asdict(replay_box.mean(DATA.box[(back, w)] for w in weeks))
    assert line["adv"] == replay_advanced.line([DATA.box[(back, w)] for w in weeks],
                                               [DATA.usage[(back, w)] for w in weeks if (back, w) in DATA.usage],
                                               per_game_td=True)
    assert by_id(at(2), stats="last4")[back]["line"]["games"] == 1
    assert by_id(at(1), stats="last4")[back]["line"]["points"] is None


def test_a_week_line_shows_points_and_box_once_played_and_the_projection_always():
    state = at(6)
    back = fakes.player_id("WR", 1)

    past, now = by_id(state, stats="week", stat_week=3)[back]["line"], by_id(state, stats="week")[back]["line"]

    assert past == {"span": "week", "week": 3, "games": 1, "points": DATA.actual[(back, 3)],
                    "projection": round(DATA.projection(back, 3), 2), "box": asdict(DATA.box[(back, 3)]),
                    "adv": replay_advanced.line([DATA.box[(back, 3)]], [DATA.usage[(back, 3)]])}
    assert (now["week"], now["points"], now["box"], now["adv"], now["games"]) == (6, None, None, None, 0)
    assert now["projection"] == round(DATA.projection(back, 6), 2)
    out = by_id(state, stats="week", stat_week=fakes.INJURED_WEEKS[0])
    assert out[fakes.INJURED_STAR_ID]["line"]["points"] is None and out[fakes.INJURED_STAR_ID]["line"]["games"] == 0
    page = wire.players(game(state), stats="week", stat_week=3, limit=1)
    assert (page["stats"], page["stat_week"]) == ("week", 3)
    assert (wire.players(game(state), stat_week=3, limit=1)["stat_week"]) is None


# ---------------- sorting ----------------

@pytest.mark.parametrize("stats", ["season", "last4", "week"])
def test_every_sort_orders_known_numbers_either_way_with_unknowns_last(stats):
    state = at(6)

    for sort in wire.SORTS:
        for direction in ("asc", "desc"):
            shown = rows(state, sort=sort, direction=direction, stats=stats)
            keys = [sort_value(row, sort) for row in shown]
            known = [key for key in keys if key is not None]
            assert keys == known + [None] * (len(keys) - len(known)), (sort, direction)
            if sort != "value":
                assert known == sorted(known, reverse=direction == "desc"), (sort, direction)
            assert len(shown) == len(DATA.as_of(6).players)


def sort_value(row: dict, sort: str):
    line = row["line"]
    if sort in replay_box.KEYS:
        return line["box"][sort] if line["box"] else None
    if sort.startswith("adv_"):
        return line["adv"][sort.removeprefix("adv_")] if line["adv"] else None
    return {"value": 0, "projection": row["projection"], "ros_ppg": row["ros_ppg"], "adp": row["adp"],
            "points": row["season_points"], "name": row["player"]["name"].lower(), "ppg": row["ppg"],
            "last": row["last_points"], "line_points": line["points"], "line_projection": line["projection"]}[sort]


def test_default_directions_and_value_order_and_paging_totals():
    state = at(6)

    best, worst = rows(state), rows(state, sort="value", direction="asc")

    assert best == rows(state, direction="desc") and worst == best[::-1]
    names = [row["player"]["name"].lower() for row in rows(state, sort="name")]
    assert names == sorted(names)
    yards = [row["line"]["box"]["rush_yds"] for row in rows(state, sort="rush_yds") if row["line"]["box"]]
    assert yards == sorted(yards, reverse=True)
    totals = {wire.players(game(state), sort=sort, direction=d, stats=s, limit=5)["total"]
              for sort in ("value", "name", "rec", "line_points") for d in ("asc", "desc") for s in wire.SPANS}
    assert totals == {wire.players(game(state), limit=5)["total"]}
    page = wire.players(game(state), status="all", sort="rec", limit=10, offset=20)
    assert page["results"] == rows(state, sort="rec")[20:30]


def test_every_advanced_stat_is_a_sort_with_players_lacking_it_last():
    state = at(6)

    assert {f"adv_{key}" for key in replay_advanced.KEYS} <= set(wire.SORTS)
    for direction in ("asc", "desc"):
        snaps = [row["line"]["adv"] and row["line"]["adv"]["snap_pct"]
                 for row in rows(state, sort="adv_snap_pct", direction=direction)]
        known = [snap for snap in snaps if snap is not None]
        assert len(known) > 50 and snaps[len(known):] == [None] * (len(snaps) - len(known))
        assert known == sorted(known, reverse=direction == "desc")
    assert rows(state, sort="adv_ypc") == rows(state, sort="adv_ypc", direction="desc")
    rushers = [row["player"]["position"] for row in rows(state, sort="adv_ypc", stats="last4")
               if row["line"]["adv"] and row["line"]["adv"]["ypc"] is not None]
    assert "RB" in rushers and not {"K", "DST"} & set(rushers)


@pytest.mark.parametrize("query, message", [
    ({"sort": "luck"}, "Unknown sort"), ({"sort": "adv_bogus"}, "Unknown sort"), ({"direction": "up"}, "Unknown dir"), ({"stats": "career"}, "Unknown stats"),
    ({"stats": "week", "stat_week": 0}, "can't be shown"), ({"stats": "week", "stat_week": 7}, "can't be shown"),
    ({"stat_week": 7}, "can't be shown"),
])
def test_bad_sort_direction_stats_or_week_are_refused(query, message):
    with pytest.raises(ValueError, match=message):
        wire.players(game(at(6)), **query)


def test_a_stat_week_before_the_season_says_no_week_is_played():
    for query in ({"stats": "week"}, {"stat_week": 1}):
        with pytest.raises(ValueError, match="No weeks have been played yet"):
            wire.players(game(drafting()), **query)
    assert wire.players(game(drafting()), limit=1)["stat_week"] is None


# ---------------- defense ratings on rows ----------------

def test_rows_carry_the_rating_of_that_weeks_opponent_as_known_before_it():
    state = at(6)
    shown = by_id(state)
    ratings = replay_defense.ratings(DATA.as_of(6))

    for row in shown.values():
        expected = None if row["bye"] else ratings.rating(row["opponent"], row["player"]["position"])
        assert row["defense"] == expected
    assert sum(row["defense"] is not None for row in shown.values()) > 200
    assert all(row["defense"] is None for row in shown.values() if row["player"]["position"] in ("K", "DST"))
    assert all(row["defense"] is None for row in rows(at(1)))        # week 1, nothing known about last season


# ---------------- the player page ----------------

def test_player_weeks_show_box_defense_and_road_games():
    state, back = at(6), fakes.player_id("RB", 1)

    weeks = wire.player(game(state), back)["weeks"]

    for row in weeks:
        week, team = row["week"], DATA.team_at(back, row["week"])
        box = DATA.box.get((back, week)) if week < 6 else None
        assert row["box"] == (asdict(box) if box else None)
        assert row["defense"] == replay_defense.ratings(DATA.as_of(week)).rating(row["opponent"], "RB")
        assert row["away"] == (DATA.schedule.game(team, week) is not None and DATA.schedule.game(team, week).away_team
                               == team)
    assert weeks[0]["defense"] is None and weeks[3]["defense"] is not None
    roads = {row["away"] for rank in range(1, 11) for row in wire.player(game(state), fakes.player_id("RB", rank))["weeks"]}
    assert roads == {True, False}             # (the fake schedule keeps each team home, or away, all season)


def test_a_played_week_after_a_trade_shows_his_new_team_and_its_opponent():
    """Known after the game: the week he first played for his new team names that team's opponent and defense."""
    pid, week = fakes.TRADED_ID, fakes.TRADED_WEEK
    old, new = DATA.team_at(pid, week), dict(DATA.team_by_week[pid])[week]
    assert old != new

    row = next(r for r in wire.player(game(at(week + 1)), pid)["weeks"] if r["week"] == week)
    before = next(r for r in wire.player(game(at(week)), pid)["weeks"] if r["week"] == week)

    opponent = DATA.schedule.opponent(new, week)
    assert (row["team"], row["opponent"]) == (new, opponent)
    assert row["defense"] == replay_defense.ratings(DATA.as_of(week)).rating(opponent, "WR")
    assert row["away"] == (DATA.schedule.game(new, week).away_team == new)
    assert before["team"] == old             # not played yet: still the team known before the week


def test_upcoming_weeks_estimate_from_the_rest_of_season_rate_and_the_defense():
    state = at(6)
    shown = game(state)
    back = fakes.player_id("RB", 1)
    value = shown.know.by_id[back]

    detail = wire.player(shown, back)
    upcoming = detail["upcoming"]

    assert [row["week"] for row in upcoming] == list(range(7, shown.settings.end_week + 1))
    assert set(upcoming[0]) == UPCOMING
    for row in upcoming:
        if row["bye"]:
            assert (row["opponent"], row["estimate"], row["defense"]) == ("BYE", None, None)
            continue
        factor = row["defense"]["factor"] if row["defense"] else 1.0
        assert row["estimate"] == round(value.ros_ppg * factor, 1) and row["out_expected"] is False
    assert any(row["bye"] for row in upcoming) and len({row["estimate"] for row in upcoming}) > 3
    assert detail["defense_basis"] == {"weeks": 5, "prior": False}


def test_an_injured_player_is_expected_out_until_the_news_says_he_is_back():
    shown = game(at(6))
    report = shown.know.by_id[fakes.INJURED_STAR_ID].news           # out since week 6, back in week 8

    upcoming = wire.player(shown, fakes.INJURED_STAR_ID)["upcoming"]

    assert report is not None and report.back == 8
    for row in upcoming:
        out = report.back is None or report.back > row["week"]
        assert row["out_expected"] is out and (row["estimate"] is None) is (out or row["bye"])
    assert [row["out_expected"] for row in upcoming[:2]] == [True, False]


def test_the_last_week_has_nothing_upcoming():
    detail = wire.player(game(finished()), fakes.player_id("QB", 1))

    assert detail["upcoming"] == []


# ---------------- no hindsight ----------------

@pytest.mark.parametrize("week", [3, 8])
def test_players_and_player_pages_ignore_everything_not_played_yet(week):
    state, hidden = at(week), hidden_changed(week)
    ids = [*fakes.STORY_IDS, fakes.player_id("RB", 1), fakes.player_id("WR", 5), fakes.player_id("QB", 2)]

    def screens(data) -> list[str]:
        shown = game(state, data)
        lists = [wire.players(shown, status="all", sort=sort, direction=direction, stats=stats, stat_week=stat_week,
                              limit=10_000)
                 for stats, stat_week in (("season", None), ("last4", None), ("week", week - 1), ("week", week))
                 for sort in wire.SORTS for direction in ("asc", "desc")]
        return [json.dumps(screen, sort_keys=True) for screen in (*lists, *(wire.player(shown, pid) for pid in ids))]

    ours, theirs = screens(DATA), screens(hidden)

    assert len(ours) == len(theirs) and all(a == b for a, b in zip(ours, theirs, strict=True))


def test_a_vague_report_keeps_him_out_every_week_to_come_and_never_reads_the_real_return(monkeypatch):
    """Review: the upcoming schedule switched from Out to points at a definite week while the news said no timetable."""
    from dataclasses import replace
    from types import MappingProxyType

    from services import replay_news
    star = fakes.INJURED_STAR_ID
    since = next(s for (pid, s) in DATA.returns if pid == star)

    def vague(seed, pid, since, back, week, tag, last=None, first_tag=None):
        return replay_news.Report(since, 9, f"Out ({tag}) since week {since}; no timetable yet.", vague=True)

    monkeypatch.setattr(replay_news, "report", vague)
    later = replace(DATA, returns=MappingProxyType({**DATA.returns, (star, since): DATA.last_week}))
    state = at(6)

    upcoming = [wire.player(game(state, data), star)["upcoming"] for data in (DATA, later)]

    assert all(row["out_expected"] and row["estimate"] is None for row in upcoming[0])
    assert upcoming[0] == upcoming[1]
