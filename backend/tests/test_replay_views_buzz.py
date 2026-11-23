"""Replay League buzz: a simulated season's public news lines (SeasonData.notes) on player rows, the draft board
and the player card, and the saved-games list's kind. No hindsight: a line dated after the current week never shows."""
import json
from dataclasses import replace
from types import MappingProxyType, SimpleNamespace

from services import replay_views as views, replay_views_league as league, replay_views_players as wire
from tests import replay_fakes as fakes
from tests.test_replay_views import DATA, USER, at, changed, drafting, every_view, game

WEEK = 5
BACK, RECEIVER, OLD_NEWS = fakes.player_id("RB", 1), fakes.player_id("WR", 2), fakes.player_id("TE", 1)


def with_notes(notes: dict[tuple[int, int], str], data=DATA):
    return replace(data, notes=MappingProxyType(notes))


NOTED = with_notes({(BACK, WEEK): "Named the starter", (RECEIVER, WEEK - 1): "Role is growing",
                    (OLD_NEWS, WEEK - 2): "Two weeks old", (BACK, 2): "Early camp buzz"})


def wire_rows(state, data) -> dict[int, dict]:
    rows = wire.players(game(state, data), status="all", limit=10_000)["results"]
    return {row["player"]["id"]: row for row in rows}


def test_a_row_carries_the_note_of_this_week_or_the_one_before():
    rows = wire_rows(at(WEEK), NOTED)

    assert rows[BACK]["buzz"] == "Named the starter"            # this week's beats an older one
    assert rows[RECEIVER]["buzz"] == "Role is growing"          # last week's still counts
    assert rows[OLD_NEWS]["buzz"] is None                       # two weeks old: gone from rows
    assert rows[fakes.player_id("QB", 1)]["buzz"] is None
    assert all(row["buzz"] is None for row in wire_rows(at(WEEK), DATA).values())    # a real replay has none


def test_roster_rows_carry_buzz_too():
    state = at(WEEK)
    mine = next(team for team in state.teams if team.id == USER)
    pid = mine.roster[0]
    shown = views.team(game(state, with_notes({(pid, WEEK): "Expect more work"})))

    assert next(row for row in shown["results"] if row["player"]["id"] == pid)["buzz"] == "Expect more work"


def test_the_player_card_lists_every_line_known_so_far_newest_first():
    later = with_notes({**NOTED.notes, (BACK, WEEK + 1): "Next week's news"})

    detail = wire.player(game(at(WEEK), later), BACK)

    assert detail["buzz"] == [{"week": WEEK, "text": "Named the starter"}, {"week": 2, "text": "Early camp buzz"}]
    assert wire.player(game(at(WEEK), DATA), BACK)["buzz"] == []


def test_a_preseason_draft_room_shows_no_buzz_even_for_week_one():
    board = views.draft(game(drafting(), with_notes({(BACK, 1): "Named the starter"})), limit=views.MAX_PAGE)

    assert all(row["buzz"] is None for row in board["results"])


def test_no_view_shows_a_note_dated_after_the_current_week():
    """The future_changed probe for notes: lines dated later than the league's week change nothing on screen."""
    hidden = with_notes({**NOTED.notes, **{(pid, week): "From the future" for pid in fakes.STORY_IDS | {BACK, RECEIVER}
                                           for week in range(WEEK + 1, 19)}})

    ours, theirs = every_view(at(WEEK), NOTED), every_view(at(WEEK), hidden)

    assert {"teams", "players", "details"} <= set(changed(at(WEEK), NOTED))    # the probe is live: notes so far show
    assert [name for name in ours if ours[name] != theirs[name]] == []
    assert not any("From the future" in screen for screen in theirs.values())


def test_saved_games_say_what_kind_of_league_each_is():
    def listed(sim_from):
        settings = {"teams": 12, "scoring": "half", **({} if sim_from == "missing" else {"sim_from": sim_from})}
        stamp = SimpleNamespace(isoformat=lambda: "2026-11-01T00:00:00")
        row = SimpleNamespace(id=1, name="L", season=2026, state=json.dumps({"phase": "draft", "week": 0, "teams": []}),
                              settings=json.dumps(settings), created_at=stamp, updated_at=stamp)
        return league.listing(row)["kind"]

    assert [listed(value) for value in ("missing", None, 10, 1)] == ["replay", "replay", "live", "future"]
