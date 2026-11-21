"""Replay League views (services/replay_views.py, replay_views_league.py): the JSON shapes, the filters,
and above all that no view shows anything from a week that has not been played.

States come from the real orchestrator on the fake season (an idle user who auto-drafts and presses the
button), built once per week and shared.
"""
import json
from dataclasses import replace
from functools import lru_cache

import pytest

from services import (
    replay_game as rg, replay_state as rs, replay_trades, replay_views as views, replay_views_league as league,
)
from tests import replay_fakes as fakes

DATA = fakes.fake_season()
SETTINGS = fakes.fake_settings(draft_slot=4)
SEED = 7
USER = SETTINGS.draft_slot
PLAYER_ROW = {"player", "opponent", "bye", "out", "projection", "points", "ros_ppg", "value", "trade_value", "rank",
              "pos_rank", "adp", "games", "ppg", "trade_locked"}
WIRE_ROW = PLAYER_ROW | {"status", "owner", "season_points", "last_points", "usage"}
SIDE = {"team_id", "name", "is_user", "projected", "points", "record"}
RECORD = {"wins", "losses", "ties", "rank", "points_for", "points_against", "place"}
PLAYER_REF = {"id", "name", "position", "team", "injury"}
SUMMARY = {"id", "name", "season", "phase", "week", "end_week", "user_team_id", "settings", "teams", "on_the_clock",
           "next_action", "record", "matchup", "pending", "champion", "updated_at", "ir_blocked", "has_injuries"}
SETTING_KEYS = {"teams", "scoring", "starters", "bench", "roster_size", "draft_slot", "waiver_mode", "difficulty",
                "regular_season_weeks", "playoff_teams", "trade_deadline_week"}
PAGE = {"total", "limit", "offset", "results"}


@lru_cache(maxsize=None)
def drafting() -> rs.State:
    return rg.create_game(SETTINGS, DATA, SEED)


@lru_cache(maxsize=None)
def at(week: int, phase: str = "open") -> rs.State:
    """The league just before `week` is played ("waivers": before that week's waiver run)."""
    if week == 1:
        return rg.auto_draft(drafting(), SETTINGS, DATA, SEED)
    before = rg.advance(at(week - 1), SETTINGS, DATA, SEED, "play")
    return before if phase == "waivers" else rg.advance(before, SETTINGS, DATA, SEED, "wednesday")


@lru_cache(maxsize=None)
def finished() -> rs.State:
    return rg.advance(at(SETTINGS.end_week), SETTINGS, DATA, SEED, "play")


def game(state: rs.State, data=DATA) -> views.Game:
    return views.Game(1, "Test league", SETTINGS, state, data, "2026-01-01T00:00:00")


def every_view(state: rs.State, data=DATA) -> dict[str, str]:
    """Every screen of a game as JSON text (which also proves each one is JSON-ready)."""
    shown, week = game(state, data), state.week
    weeks = range(1, SETTINGS.end_week + 1) if state.phase != "draft" else ()
    rostered = [pid for team in state.teams for pid in team.roster]
    screens = {
        "summary": views.summary(shown), "draft": views.draft(shown, limit=views.MAX_PAGE),
        "teams": [views.team(shown, team.id, w) for team in state.teams for w in range(min(1, week), week + 1)],
        "matchups": [views.matchup(shown, w) for w in range(1, week + 1)],
        "scoreboards": [views.scoreboard(shown, w) for w in weeks],
        "standings": league.standings(shown), "schedule": league.schedule(shown),
        "activity": league.activity(shown, limit=10_000),
        "players": [league.players(shown, status="all", sort=sort, limit=10_000) for sort in league.SORTS],
        "details": [league.player(shown, pid) for pid in (*rostered[:40], *fakes.STORY_IDS)],
        "claims": league.claims(shown), "trades": league.trades(shown),
    }
    return {name: json.dumps(screen, sort_keys=True, allow_nan=False) for name, screen in screens.items()}


def changed(state: rs.State, data) -> list[str]:
    """The screens that look different on `data` than on the real fake season. (Names only: pytest's diff
    of two multi-megabyte strings never finishes.)"""
    ours, theirs = every_view(state), every_view(state, data)
    return [name for name in ours if ours[name] != theirs[name]]


def hidden_changed(week: int):
    """The season with everything a league in `week` must not know changed: real points and usage from that
    week on, projections after it."""
    shaken = fakes.future_changed(DATA, after_week=week)
    usage = {key: replace(u, targets=u.targets + 5) if key[1] == week else u for key, u in shaken.usage.items()}
    return replace(fakes.patched(shaken, actual={key: points + 7.5 for key, points in DATA.actual.items()
                                                 if key[1] == week}), usage=usage)


# ---------------- no hindsight ----------------

@pytest.mark.parametrize("week, phase", [(1, "open"), (5, "waivers"), (5, "open"), (9, "open"), (16, "open")])
def test_no_view_changes_when_the_unplayed_part_of_the_season_changes(week, phase):
    state = at(week, phase)

    assert changed(state, hidden_changed(week)) == []


def test_the_draft_room_shows_nothing_from_after_the_draft():
    assert changed(drafting(), fakes.future_changed(DATA, after_week=1)) == []


def test_the_probe_is_live_a_played_week_and_this_weeks_projection_do_show():
    state = at(5)
    last_week = fakes.patched(DATA, actual={key: points + 7.5 for key, points in DATA.actual.items() if key[1] == 4})
    this_week = fakes.patched(DATA, projected={key: points + 7.5 for key, points in DATA.projected.items()
                                               if key[1] == 5})

    assert {"teams", "matchups", "players", "details"} <= set(changed(state, last_week))
    assert {"summary", "teams", "matchups", "scoreboards", "players", "details"} <= set(changed(state, this_week))
    used = replace(DATA, usage={key: replace(u, carries=u.carries + 9) if key[1] == 4 else u
                                for key, u in DATA.usage.items()})
    assert set(changed(state, used)) == {"players", "details"}
    starter = next(pid for ids in state.results[3].starters[1].values() for pid in ids    # team 1's, week 4
                   if pid in state.teams[0].roster)
    tag_now, tag_before = (fakes.tagged(DATA, {(starter, week): fakes.Injury("Q")}) for week in (5, 4))
    assert {"teams", "players", "details", "activity"} <= set(changed(state, tag_now))
    assert {"teams", "details"} <= set(changed(state, tag_before)) <= {"teams", "matchups", "details"}


def test_a_week_not_reached_yet_cannot_be_opened():
    shown = game(at(5))

    for build in (lambda: views.team(shown, week=6), lambda: views.matchup(shown, week=6),
                  lambda: shown.know_at(6), lambda: views.team(shown, week=0)):
        with pytest.raises(ValueError, match="week 5"):
            build()
    future = views.scoreboard(shown, 6)
    assert future["played"] is False and len(future["results"]) == 6
    assert all(side["projected"] is None and side["points"] is None
               for row in future["results"] for side in (row["home"], row["away"]))


# ---------------- the game and the draft ----------------

def test_summary_follows_the_game_from_the_draft_to_the_champion():
    draft, waivers, open_, done = (views.summary(game(state)) for state in (drafting(), at(3, "waivers"), at(3),
                                                                           finished()))

    assert set(draft) == SUMMARY and set(draft["record"]) == RECORD and set(open_["settings"]) == SETTING_KEYS
    assert (draft["ir_blocked"], draft["has_injuries"], open_["ir_blocked"], open_["has_injuries"]) == ([], True,
                                                                                                       [], True)
    assert (draft["phase"], draft["on_the_clock"], draft["matchup"]) == ("draft", USER, None)
    assert [s["next_action"]["step"] for s in (draft, waivers, open_, done)] == ["pick", "wednesday", "play", "done"]
    assert open_["next_action"]["label"] == "Play week 3" and open_["week"] == 3
    assert waivers["next_action"] == {"step": "wednesday", "label": "Sim to Wednesday"}
    assert set(open_["matchup"]) == {"week", "playoff", "label", "you", "opponent"}
    assert set(open_["matchup"]["you"]) == SIDE and open_["matchup"]["you"]["team_id"] == USER
    assert open_["matchup"]["you"]["points"] is None and open_["matchup"]["you"]["projected"] > 50
    assert open_["record"]["wins"] + open_["record"]["losses"] + open_["record"]["ties"] == 2
    assert open_["settings"]["starters"]["FLEX"] == 1 and open_["settings"]["roster_size"] == 15
    assert done["champion"]["team_id"] == finished().champion and done["record"]["place"] in range(1, 13)
    assert done["matchup"]["you"]["points"] is not None and open_["champion"] is None


def test_draft_board_lists_who_is_left_in_adp_order_with_filters_and_paging():
    shown = game(drafting())

    board = views.draft(shown, limit=20)
    backs = views.draft(shown, position="RB", limit=500)
    flex = views.draft(shown, position="FLEX", limit=500)
    named = views.draft(shown, search="  rePLAY qb1", limit=500)
    second = views.draft(shown, limit=5, offset=5)

    adps = [row["adp"] for row in board["results"]]
    assert set(board) >= PAGE | {"phase", "on_the_clock", "is_user_turn", "round", "overall", "rounds",
                                 "total_picks", "order", "picks", "roster"}
    assert adps == sorted(adps) and board["is_user_turn"] and (board["round"], board["overall"]) == (1, USER)
    assert board["total"] == len(DATA.as_of(0).players) - len(board["picks"]) and len(board["picks"]) == USER - 1
    assert set(board["results"][0]) == {"player", "adp", "pos_rank", "rank", "projection"}
    assert {row["player"]["position"] for row in backs["results"]} == {"RB"}
    assert {row["player"]["position"] for row in flex["results"]} == {"RB", "WR", "TE"}
    assert named["total"] >= 10 and all("Replay QB1" in row["player"]["name"] for row in named["results"])
    assert second["results"] == views.draft(shown, limit=10)["results"][5:]
    assert [team["team_id"] for team in board["order"]] == list(range(1, 13))
    with pytest.raises(ValueError, match="Unknown position"):
        views.draft(shown, position="LB")


def test_draft_recap_after_the_draft_has_every_pick_and_nobody_left():
    recap = views.draft(game(at(1)))

    assert recap["total"] == 0 and recap["on_the_clock"] is None and recap["overall"] is None
    assert len(recap["picks"]) == 180 and recap["picks"][0]["team"]["team_id"] == 1
    assert sum(len(players) for players in recap["roster"].values()) == 15


# ---------------- team, matchup, scoreboard ----------------

def test_team_lists_starters_in_slot_order_then_the_bench():
    state = at(3)
    roster = views.team(game(state))

    rows = roster["results"]
    starters = [row for row in rows if row["starting"]]
    assert set(roster) == {"team", "week", "played", "manual_lineup", "projected_total", "actual_total", "record",
                           "results", "ir"} and roster["ir"] == []
    assert set(rows[0]) == PLAYER_ROW | {"slot", "starting"} and set(rows[0]["player"]) == PLAYER_REF
    assert [row["slot"] for row in starters] == ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DST"]
    assert [row["slot"] for row in rows[9:]] == ["BN"] * 6 and len(rows) == 15
    assert roster["projected_total"] == pytest.approx(sum(row["projection"] for row in starters), abs=0.05)
    assert (roster["week"], roster["played"], roster["actual_total"], roster["manual_lineup"]) == (3, False, None,
                                                                                                   False)
    assert all(row["points"] is None for row in rows)
    assert {row["player"]["id"] for row in rows} == set(rs.user_team(state).roster)
    assert views.team(game(state), team_id=9)["team"] == {"team_id": 9, "name": state.teams[8].name, "is_user": False}
    with pytest.raises(ValueError, match="No team 99"):
        views.team(game(state), team_id=99)


def test_team_in_a_past_week_shows_what_was_started_and_what_it_scored():
    state = at(6)
    result = state.results[2]                                    # week 3

    past = views.team(game(state), team_id=2, week=3)

    started = [pid for ids in result.starters[2].values() for pid in ids]
    assert [row["player"]["id"] for row in past["results"]] == started and past["played"] is True
    assert past["actual_total"] == result.scores[2]
    assert sum(row["points"] or 0.0 for row in past["results"]) == pytest.approx(result.scores[2], abs=0.01)
    assert all(row["projection"] == round(DATA.projection(row["player"]["id"], 3), 2) for row in past["results"])


def test_a_manual_lineup_is_flagged_and_shown():
    state = at(2)
    best = views.team(game(state))["results"]
    flex, bench = next(row for row in best if row["slot"] == "FLEX"), next(
        row for row in best if not row["starting"] and row["player"]["position"] in ("RB", "WR"))
    starters = {slot: [row["player"]["id"] for row in best if row["slot"] == slot] for slot in SETTINGS.starters}
    starters["FLEX"] = [bench["player"]["id"]]

    manual = views.team(game(rg.set_lineup(state, SETTINGS, DATA, starters)))

    slots = {row["player"]["id"]: row["slot"] for row in manual["results"]}
    assert manual["manual_lineup"] is True
    assert slots[bench["player"]["id"]] == "FLEX" and slots[flex["player"]["id"]] == "BN"


def test_out_and_bye_players_are_flagged_with_no_projection():
    star = lambda week: league.player(game(at(week)), fakes.INJURED_STAR_ID)["player"]      # noqa: E731

    healthy, out = star(5), star(7)
    resting = [row for row in league.players(game(at(5)), status="all", limit=1000)["results"] if row["bye"]]

    assert (healthy["out"], healthy["bye"]) == (False, False) and healthy["projection"] > 10
    assert (out["out"], out["bye"], out["projection"]) == (True, False, 0.0) and out["opponent"] != "BYE"
    assert out["ros_ppg"] > 10 and 0 < out["value"] < healthy["value"]
    assert len(resting) > 20 and all(row["opponent"] == "BYE" and row["projection"] == 0 for row in resting)
    assert sum(row["out"] for row in resting) < 5                # out before the bye: both flags


def test_matchup_and_scoreboard_show_both_sides_and_the_winner_once_played():
    shown = game(at(6))

    live, past, board = views.matchup(shown), views.matchup(shown, week=2, team_id=7), views.scoreboard(shown, 2)

    assert set(live) == {"week", "played", "playoff", "label", "winner", "home", "away"}
    assert USER in (live["home"]["team"]["team_id"], live["away"]["team"]["team_id"])
    assert (live["played"], live["winner"], live["label"]) == (False, None, None)
    assert len(live["home"]["results"]) == 15                    # the current week includes the bench
    sides = {side["team"]["team_id"]: side["actual_total"] for side in (past["home"], past["away"])}
    assert 7 in sides and past["winner"] == max(sides, key=sides.get) and past["played"] is True
    assert set(board) == {"week", "played", "playoff", "results"} and set(board["results"][0]) == {
        "home", "away", "winner", "label"}
    assert set(board["results"][0]["home"]) == SIDE
    teams = [side["team_id"] for row in board["results"] for side in (row["home"], row["away"])]
    assert sorted(teams) == list(range(1, 13))
    assert all(side["points"] == at(6).results[1].scores[side["team_id"]]
               for row in board["results"] for side in (row["home"], row["away"]))


# ---------------- standings, schedule, activity ----------------

def test_standings_run_from_seeds_to_the_final_order_with_the_bracket():
    early, done = league.standings(game(at(4))), league.standings(game(finished()))

    assert set(early) == {"results", "regular_season_weeks", "playoff_teams", "seeds_final", "bracket", "champion"}
    assert set(early["results"][0]) == RECORD | {"team_id", "name", "is_user", "seed", "in_playoffs"}
    assert [row["seed"] for row in early["results"]] == list(range(1, 13))
    assert (early["seeds_final"], early["bracket"], early["champion"]) == (False, [], None)
    assert all(row["place"] is None for row in early["results"])
    assert [row["place"] for row in done["results"]] == list(range(1, 13)) and done["seeds_final"] is True
    assert done["results"][0]["team_id"] == done["champion"]["team_id"] == finished().champion
    assert sorted(row["seed"] for row in done["results"][:4]) == [1, 2, 3, 4]
    assert [board["week"] for board in done["bracket"]] == [15, 16]
    assert [row["label"] for row in done["bracket"][0]["results"]] == ["Semi-final"] * 2 + ["Consolation"] * 4
    assert [row["label"] for row in done["bracket"][1]["results"]] == ["Championship", "Third place", "5th place",
                                                                     "7th place", "9th place", "11th place"]
    assert done["bracket"][1]["results"][0]["winner"] == finished().champion


def test_schedule_lists_every_week_with_results_so_far():
    mid, done = league.schedule(game(at(6))), league.schedule(game(finished()), team_id=3)

    rows = mid["results"]
    assert [row["week"] for row in rows] == list(range(1, 17)) and mid["team"]["team_id"] == USER
    assert set(rows[0]) == {"week", "playoff", "played", "opponent", "points", "opponent_points", "result"}
    assert all(row["result"] in ("W", "L", "T") and row["points"] is not None for row in rows[:5])
    assert all(row["result"] is None and row["points"] is None for row in rows[5:])
    assert all(row["opponent"] for row in rows[:14]) and [row["opponent"] for row in rows[14:]] == [None, None]
    assert [row["playoff"] for row in rows] == [False] * 14 + [True] * 2
    assert all(row["played"] and row["opponent"] for row in done["results"])
    assert league.schedule(game(drafting()))["results"] == []


def test_activity_is_newest_first_and_filters_by_kind():
    shown = game(at(6))

    everything = league.activity(shown, limit=10_000)
    moves = league.activity(shown, kind="add, drop", limit=10_000)
    paged = league.activity(shown, kind="draft", limit=7, offset=3)

    ids = [event["id"] for event in everything["results"]]
    assert set(everything) == PAGE and ids == sorted(ids, reverse=True) and everything["total"] == len(at(6).events)
    assert set(everything["results"][0]) == {"id", "week", "kind", "team", "text", "players"}
    kinds = {event["kind"] for event in moves["results"]}
    assert "add" in kinds and kinds <= {"add", "drop"} and moves["total"] > 5    # an add-with-drop is one "add"
    assert all(event["players"] and event["team"] for event in moves["results"])
    assert (paged["total"], len(paged["results"])) == (180, 7)
    assert paged["results"][0]["id"] == league.activity(shown, kind="draft", limit=4)["results"][3]["id"]
    with pytest.raises(ValueError, match="Unknown activity kind"):
        league.activity(shown, kind="gossip")


def test_new_events_are_the_ones_an_action_added_oldest_first():
    before, after = at(5), at(6, "waivers")

    events = views.new_events(game(after), before)

    assert [event["id"] for event in events] == [e.id for e in after.events[len(before.events):]]
    assert {event["kind"] for event in events} >= {"result"} and len(events) >= 6
    assert len(views.new_events(game(after), None)) == len(after.events)


# ---------------- players ----------------

def test_players_default_to_the_available_ones_best_value_first():
    state = at(6, "waivers")
    shown = game(state)

    wire, everyone = league.players(shown, limit=1000), league.players(shown, status="all", limit=1000)
    taken = league.players(shown, status="rostered", limit=1000)

    rostered = rs.rostered(state)
    assert set(wire) == PAGE | {"week"} and set(wire["results"][0]) == WIRE_ROW
    known = len(DATA.as_of(6).players)                     # the pool as known in week 6, not the whole season's
    assert wire["total"] == known - 180 and everyone["total"] == known < len(DATA.players)
    assert all(row["status"] == "waivers" and row["owner"] is None for row in wire["results"])
    assert not {row["player"]["id"] for row in wire["results"]} & rostered
    assert [row["rank"] for row in wire["results"]] == sorted(row["rank"] for row in wire["results"])
    assert taken["total"] == 180 and all(row["status"] == "rostered" and row["owner"] for row in taken["results"])
    assert league.players(game(at(6)), status="free_agent", limit=1)["total"] > 100
    assert league.players(game(at(6)), status="waivers", limit=1)["total"] == len(at(6).locked) == 0


def test_players_show_real_points_only_for_weeks_already_played():
    rows = league.players(game(at(6)), status="all", limit=1000)["results"]

    for row in rows[::7]:
        pid = row["player"]["id"]
        played = [DATA.actual[(pid, week)] for week in range(1, 6) if (pid, week) in DATA.actual]
        assert row["season_points"] == pytest.approx(sum(played), abs=0.01) and row["games"] == len(played)
        assert row["last_points"] == DATA.actual.get((pid, 5)) and row["points"] is None


def test_players_filter_by_position_group_and_name_and_sort():
    shown = game(at(6))

    def listed(**filters) -> list[dict]:
        return league.players(shown, status="all", limit=1000, **filters)["results"]

    assert {row["player"]["position"] for row in listed(position="FLEX")} == {"RB", "WR", "TE"}
    assert {row["player"]["position"] for row in listed(position="DST")} == {"DST"}
    assert [row["player"]["name"] for row in listed(search="BREAKOUT")] == ["Breakout Back"]
    for sort, key in (("projection", lambda r: -r["projection"]), ("ros_ppg", lambda r: -r["ros_ppg"]),
                      ("points", lambda r: -r["season_points"]), ("name", lambda r: r["player"]["name"].lower()),
                      ("adp", lambda r: (r["adp"] is None, r["adp"] or 0))):
        keys = [key(row) for row in listed(sort=sort)]
        assert keys == sorted(keys), sort
    page = league.players(shown, status="all", sort="name", limit=10, offset=20)
    assert page["results"] == listed(sort="name")[20:30] and (page["limit"], page["offset"]) == (10, 20)
    for bad in ({"position": "LB"}, {"status": "injured"}, {"sort": "luck"}):
        with pytest.raises(ValueError, match="Unknown"):
            league.players(shown, **bad)


def test_player_page_has_his_weeks_so_far_and_who_owns_him():
    state = at(6)
    owned = rs.user_team(state).roster[0]

    detail, never = league.player(game(state), owned), league.player(game(state), fakes.NEVER_PLAYED_ID)

    weeks = detail["weeks"]
    assert set(detail) == {"player", "weeks"} and set(detail["player"]) == WIRE_ROW
    assert detail["player"]["owner"]["team_id"] == USER and detail["player"]["status"] == "rostered"
    assert [row["week"] for row in weeks] == [1, 2, 3, 4, 5, 6]
    assert set(weeks[0]) == {"week", "played", "team", "opponent", "bye", "out", "projection", "points", "ros_ppg",
                             "value", "trade_value", "rank", "pos_rank", "injury", "usage"}
    assert [row["played"] for row in weeks] == [True] * 5 + [False] and weeks[5]["points"] is None
    assert all(row["points"] == DATA.actual.get((owned, row["week"])) for row in weeks[:5])
    assert all(row["projection"] == round(DATA.projection(owned, row["week"]), 2) for row in weeks)
    assert all(row["points"] is None and row["projection"] == 0 for row in never["weeks"])
    assert league.player(game(drafting()), owned)["weeks"] == []
    with pytest.raises(rs.ReplayNotFound, match="no player 5"):
        league.player(game(state), 5)


# ---------------- claims and trades ----------------

def test_claims_are_listed_in_the_users_order_with_the_waiver_priority():
    state = at(4, "waivers")
    wire = [row["player"]["id"] for row in league.players(game(state), limit=2)["results"]]
    drop = rs.user_team(state).roster[-3]
    claimed = rg.submit_claim(rg.submit_claim(state, SETTINGS, DATA, wire[0], drop), SETTINGS, DATA, wire[1], drop)

    listed, idle = league.claims(game(claimed)), league.claims(game(at(4)))

    assert set(listed) == {"phase", "can_claim", "priority", "waiver_order", "faab", "results"}
    assert [row["add"]["player"]["id"] for row in listed["results"]] == wire
    assert [row["order"] for row in listed["results"]] == [1, 2] and listed["can_claim"] is True
    assert set(listed["results"][0]) == {"id", "order", "add", "drop", "bid"}
    assert listed["faab"] is None and [row["bid"] for row in listed["results"]] == [0, 0]
    assert set(listed["results"][0]["add"]) == PLAYER_ROW
    assert listed["results"][0]["drop"]["player"]["id"] == drop
    assert listed["waiver_order"][listed["priority"] - 1]["team_id"] == USER and len(listed["waiver_order"]) == 12
    assert (idle["can_claim"], idle["results"]) == (False, [])


def _swap(state: rs.State, partner: int) -> tuple[list[int], list[int]]:
    """The user's and the partner's most valuable running backs."""
    know = rg.knowledge(state, SETTINGS, DATA)

    def best_back(team_id: int) -> int:
        return max((pid for pid in rs.team(state, team_id).roster if know.by_id[pid].position == "RB"),
                   key=lambda pid: know.by_id[pid].market_value)

    return [best_back(USER)], [best_back(partner)]


def test_verdict_is_told_from_the_users_side():
    state = at(3)
    give, get = _swap(state, 9)
    found = rg.evaluate_trade(state, SETTINGS, DATA, 9, give, get)

    shown = league.verdict(game(state), found)

    assert set(shown) == {"accepted", "reasons", "fair_label", "value_give", "value_get", "percent", "your_gain",
                          "their_gain", "give", "get", "your_drops", "their_drops", "suggestion"}
    assert [row["player"]["id"] for row in shown["give"]] == give and [r["player"]["id"] for r in shown["get"]] == get
    assert shown["give"][0]["value"] == shown["value_give"] and shown["accepted"] == found.accepted
    assert isinstance(shown["reasons"], list) and (shown["accepted"] or shown["reasons"])


def test_trades_show_pending_offers_with_a_preview_and_the_history_newest_first():
    state = at(3)
    mine, theirs = _swap(state, 9)
    offered = replay_trades.record_offer(state, 9, USER, theirs, mine, "pending")
    declined = replace(offered, offers=(*offered.offers, replace(offered.offers[-1], id=9001, status="declined")))

    screen = league.trades(game(declined))

    offer = screen["offers"][0]
    assert set(screen) == {"open", "deadline_week", "max_players", "out_discount", "offers", "results"}
    assert screen["out_discount"] == replay_trades.OUT_RISK_DISCOUNT == 0.8
    assert set(offer) == {"id", "week", "status", "from_team", "to_team", "give", "get", "verdict", "note"}
    assert (offer["status"], offer["from_team"]["team_id"], offer["to_team"]["is_user"]) == ("pending", 9, True)
    assert [p["id"] for p in offer["give"]] == theirs and [p["id"] for p in offer["get"]] == mine
    assert [row["player"]["id"] for row in offer["verdict"]["give"]] == mine and offer["note"] is None
    assert screen["results"][0]["id"] == 9001 and offer["id"] not in [row["id"] for row in screen["results"]]
    assert [row["id"] for row in screen["results"]] == sorted((row["id"] for row in screen["results"]), reverse=True)
    assert screen["open"] is True and league.trades(game(at(12)))["open"] is False


def test_an_offer_that_cannot_be_judged_gets_a_note_instead_of_a_verdict():
    state = at(3)
    mine, theirs = _swap(state, 9)
    spare = [pid for pid in rs.team(state, 9).roster if pid not in theirs][:1]
    lopsided = replay_trades.record_offer(state, 9, USER, [*theirs, *spare], mine, "pending")    # overfills the user

    offer = league.trades(game(lopsided))["offers"][0]

    assert offer["verdict"] is None and "roster limit" in offer["note"]


# ---------------- injury tags and the IR slot ----------------

def _refs(screen) -> list[dict]:
    """Every PlayerRef anywhere in a screen."""
    if isinstance(screen, dict):
        found = [screen] if set(screen) == PLAYER_REF else []
        return found + [ref for value in screen.values() for ref in _refs(value)]
    return [ref for value in screen for ref in _refs(value)] if isinstance(screen, list) else []


def _on_ir(state: rs.State, player_id: int) -> rs.State:
    """The user's `player_id` moved from the active roster to the IR slot (straight on the state)."""
    owner = rs.user_team(state)
    return rs.with_team(state, replace(owner, roster=tuple(p for p in owner.roster if p != player_id),
                                       ir=(player_id,)))


def test_the_draft_room_never_shows_a_tag_even_one_known_in_week_one():
    drafted = [pick.player_id for pick in at(1).picks[:30]]
    hurt = fakes.tagged(DATA, {(pid, 1): fakes.Injury("IR", None, "Knee") for pid in drafted})

    during, after = views.draft(game(drafting(), hurt), limit=views.MAX_PAGE), views.draft(game(at(1), hurt))

    assert len(_refs(during)) > 100 and len(_refs(after)) >= 180
    assert all(ref["injury"] is None for ref in _refs(during) + _refs(after))
    assert any(ref["injury"] == {"tag": "IR", "practice": None, "injury": "Knee"}
               for team in at(1).teams for ref in _refs(views.team(game(at(1), hurt), team.id)))   # week 1: shown


def test_has_injuries_says_whether_any_tag_is_stored_for_the_season():
    bare = fakes.tagged(DATA, {key: None for key in DATA.injuries})

    assert views.summary(game(at(1)))["has_injuries"] is True             # the first tag is in week 3
    assert views.summary(game(at(3), bare))["has_injuries"] is False


def test_tags_are_this_weeks_on_refs_and_that_weeks_on_a_players_weeks():
    shown = game(at(11))
    star = fakes.INJURED_STAR_ID

    weeks = league.player(shown, star)["weeks"]
    drafted = next(e for e in league.activity(shown, kind="draft", limit=500)["results"]
                   if e["players"][0]["id"] == star)
    earlier = next(e for e in league.activity(game(at(7)), kind="draft", limit=500)["results"]
                   if e["players"][0]["id"] == star)

    assert [row["injury"] and row["injury"]["tag"] for row in weeks] == [None] * 5 + ["O"] * 4 + ["Q", None]
    assert weeks[5]["injury"] == {"tag": "O", "practice": "DNP", "injury": "Knee"}
    assert drafted["players"][0]["injury"] is None                         # an old event: today's tag (none)
    assert earlier["players"][0]["injury"] == {"tag": "O", "practice": "DNP", "injury": "Knee"}


def test_an_ir_player_is_listed_apart_this_week_only_and_keeps_his_owner():
    state = at(7)
    player = next(pid for pid in rs.user_team(state).roster if (pid, 7) not in DATA.injuries)
    moved, hurt = _on_ir(state, player), fakes.tagged(DATA, {(player, 7): fakes.Injury("IR", None, "Ankle")})

    now, past = views.team(game(moved, hurt)), views.team(game(moved, hurt), week=6)
    page = league.player(game(moved, hurt), player)["player"]

    assert [(row["slot"], row["starting"], row["player"]["id"]) for row in now["ir"]] == [("IR", False, player)]
    assert set(now["ir"][0]) == PLAYER_ROW | {"slot", "starting"} and now["ir"][0]["out"] is True
    assert now["ir"][0]["player"]["injury"] == {"tag": "IR", "practice": None, "injury": "Ankle"}
    assert player not in [row["player"]["id"] for row in now["results"]] and len(now["results"]) == 14
    assert past["ir"] == [] and (page["owner"]["team_id"], page["status"]) == (USER, "rostered")
    assert league.activity(game(moved), kind="add,drop,ir")["total"] >= 0          # "ir" is a known kind


def test_an_ir_player_without_his_tag_blocks_the_user():
    state = at(7)
    player = next(pid for pid in rs.user_team(state).roster if (pid, 7) not in DATA.injuries)
    moved = _on_ir(state, player)

    blocked = views.summary(game(moved))["ir_blocked"]
    still_hurt = views.summary(game(moved, fakes.tagged(DATA, {(player, 7): fakes.Injury("O")})))["ir_blocked"]

    assert [ref["id"] for ref in blocked] == [player] and set(blocked[0]) == PLAYER_REF
    assert still_hurt == []


def test_an_offer_for_a_player_since_moved_to_ir_says_why_it_cannot_be_accepted():
    state = at(3)
    mine, theirs = _swap(state, 9)
    offered = _on_ir(replay_trades.record_offer(state, 9, USER, theirs, mine, "pending"), mine[0])     # IR-26

    offer = league.trades(game(offered))["offers"][0]

    name = rg.knowledge(state, SETTINGS, DATA).by_id[mine[0]].name
    assert (offer["verdict"], offer["note"]) == (None, f"On IR, so not tradable until activated: {name}.")
