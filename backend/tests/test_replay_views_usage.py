"""Replay League views, round 2: usage numbers, FAAB on the claims screen and the consolation place labels."""
from dataclasses import replace
from statistics import mean

import pytest

from services import replay_state as rs, replay_views as views, replay_views_league as league
from tests import replay_fakes as fakes
from tests.test_replay_views import DATA, SETTINGS, USER, at, finished, game, hidden_changed

USAGE = {"games", "snap_pct", "targets", "target_share", "carries"}


def expected(player_id: int, before: int, data=DATA) -> dict | None:
    """The Usage shape over his last USAGE_GAMES games before week `before`, computed the long way."""
    weeks = sorted((w for pid, w in data.usage if pid == player_id and w < before), reverse=True)[:league.USAGE_GAMES]
    return shape([data.usage[(player_id, w)] for w in weeks])


def shape(games: list) -> dict | None:
    if not games:
        return None
    snaps = [g.snap_pct for g in games if g.snap_pct is not None]
    shares = [g.target_share for g in games if g.target_share is not None]
    return {"games": len(games), "snap_pct": round(mean(snaps), 1) if snaps else None,
            "targets": round(mean(g.targets for g in games), 1),
            "target_share": round(mean(shares), 3) if shares else None,
            "carries": round(mean(g.carries for g in games), 1)}


def wire(state: rs.State, data=DATA) -> dict[int, dict]:
    rows = league.players(game(state, data), status="all", limit=10_000)["results"]
    return {row["player"]["id"]: row for row in rows}


# ---------------- usage ----------------

def test_wire_rows_average_the_last_three_games_before_this_week():
    rows = wire(at(6))
    back = fakes.player_id("RB", 1)

    assert set(rows[back]["usage"]) == USAGE and rows[back]["usage"]["games"] == 3
    assert all(row["usage"] == expected(pid, 6) for pid, row in rows.items())
    assert all(row["usage"] is None for row in rows.values() if row["player"]["position"] in ("K", "DST"))
    assert rows[fakes.NEVER_PLAYED_ID]["usage"] is None


def test_before_week_one_is_played_nobody_has_usage_and_week_two_has_one_game():
    assert all(row["usage"] is None for row in wire(at(1)).values())
    assert {row["usage"]["games"] for row in wire(at(2)).values() if row["usage"]} == {1}


def test_snap_pct_and_target_share_average_only_the_games_that_have_them():
    back = fakes.player_id("RB", 2)
    last_three = sorted((w for pid, w in DATA.usage if pid == back and w < 5), reverse=True)[:3]
    gaps = {(back, w) for w in last_three[:2]}
    data = replace(DATA, usage={key: replace(u, snap_pct=None, target_share=None) if key in gaps else u
                                for key, u in DATA.usage.items()})
    none_at_all = replace(DATA, usage={key: replace(u, snap_pct=None, target_share=None)
                                       for key, u in DATA.usage.items()})

    shown, empty = wire(at(5), data)[back]["usage"], wire(at(5), none_at_all)[back]["usage"]

    assert shown == expected(back, 5, data) and shown["snap_pct"] == DATA.usage[(back, last_three[2])].snap_pct
    assert (empty["snap_pct"], empty["target_share"], empty["games"]) == (None, None, 3)


def test_player_weeks_show_each_played_weeks_own_game_and_never_the_current_week():
    state, back = at(6), fakes.player_id("WR", 1)

    weeks = league.player(game(state), back)["weeks"]

    for row in weeks[:5]:
        usage = DATA.usage.get((back, row["week"]))
        assert row["usage"] == shape([usage] if usage else [])
    assert weeks[-1]["week"] == 6 and weeks[-1]["usage"] is None and (back, 6) in DATA.usage
    assert all(row["usage"] is None for row in league.player(game(state), fakes.player_id("K", 1))["weeks"])


@pytest.mark.parametrize("week, phase", [(3, "open"), (6, "waivers")])
def test_usage_never_shows_the_current_week_or_later(week, phase):
    state, shaken = at(week, phase), hidden_changed(week)
    ids = list(fakes.STORY_IDS) + [fakes.player_id("RB", 1), fakes.player_id("WR", 1)]

    assert wire(state, shaken) == wire(state)
    assert [league.player(game(state, shaken), pid) for pid in ids] == [league.player(game(state), pid) for pid in ids]


# ---------------- FAAB on the claims screen ----------------

def faab_game(state: rs.State) -> views.Game:
    return views.Game(1, "FAAB league", replace(SETTINGS, waiver_mode="faab"), state, DATA)


def test_claims_carry_bids_and_every_teams_budget_left_in_waiver_order():
    state = at(4, "waivers")
    wire_ids = [row["player"]["id"] for row in league.players(game(state), limit=2)["results"]]
    mine = rs.Claim(9001, USER, wire_ids[0], None, 12)
    theirs = rs.Claim(9002, 3, wire_ids[1], None, 55)               # never in the state today: still never shown
    spent = replace(state, claims=(mine, theirs), faab_spent={USER: 30, 3: 7})

    screen = league.claims(faab_game(spent))

    assert [(row["id"], row["bid"]) for row in screen["results"]] == [(9001, 12)]
    assert screen["faab"]["budget"] == rs.FAAB_BUDGET and screen["faab"]["left"] == rs.FAAB_BUDGET - 30
    assert [row["team"]["team_id"] for row in screen["faab"]["teams"]] == list(state.waiver_order)
    assert {row["team"]["team_id"]: row["left"] for row in screen["faab"]["teams"]}[3] == rs.FAAB_BUDGET - 7
    assert "55" not in str(screen["faab"]) and set(screen["faab"]["teams"][0]) == {"team", "left"}


def test_claims_have_no_faab_block_outside_faab_leagues():
    assert league.claims(game(at(4, "waivers")))["faab"] is None


# ---------------- playoff labels ----------------

@pytest.mark.parametrize("teams, final_week", [
    (10, ["Championship", "Third place", "5th place", "7th place", "9th place"]),
    (12, ["Championship", "Third place", "5th place", "7th place", "9th place", "11th place"]),
    (14, ["Championship", "Third place", "5th place", "7th place", "9th place", "11th place", "13th place"]),
])
def test_playoff_week_two_names_every_place_game(teams, final_week):
    settings = fakes.fake_settings(teams=teams)
    first, second = settings.regular_season_weeks + 1, settings.regular_season_weeks + 2

    assert [views.label(settings, first, i) for i in range(teams // 2)] == \
        ["Semi-final"] * 2 + ["Consolation"] * (teams // 2 - 2)
    assert [views.label(settings, second, i) for i in range(teams // 2)] == final_week
    assert views.label(settings, settings.regular_season_weeks, 0) is None


def test_the_finished_seasons_final_week_labels_match():
    board = views.scoreboard(game(finished()), SETTINGS.end_week)

    assert [row["label"] for row in board["results"]][2:] == ["5th place", "7th place", "9th place", "11th place"]
