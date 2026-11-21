"""Replay League state: frozen records, exact JSON round trip, seeded randomness, standings and the store."""
import json
from collections import Counter
from dataclasses import replace
from datetime import timedelta

import numpy as np
import pytest
from sqlalchemy.orm import Session

from services import replay_state as rs
from services.replay_state import Claim, Event, Offer, Pick, Settings, State, Team, WeekResult


def make_state(**overrides) -> State:
    teams = (Team(1, "You", True, (11, 12, 13), 0.0, {"QB": 1.0, "RB": 1.0}),
             Team(2, "CPU Two", False, (21, 22), 1.25, {"QB": 0.93, "RB": 1.08}),
             Team(3, "CPU Three", False, (31,), 0.6, {"QB": 1.1}),
             Team(4, "CPU Four", False, (), 1.4, {}))
    week_one = WeekResult(week=1, matchups=((1, 2), (3, 4)), scores={1: 101.5, 2: 88.25, 3: 70.0, 4: 70.0},
                          starters={1: {"QB": (11,), "RB": (12, 13)}, 2: {"QB": (21,)}, 3: {}, 4: {}},
                          points={11: 24.6, 12: 0.1, 13: 7.3, 21: 30.0}, playoff=False)
    fields = dict(
        phase="open", week=2, teams=teams, draft_order=(3, 1, 4, 2),
        picks=(Pick(1, 1, 3, 31), Pick(2, 1, 1, 11)),
        schedule=(((1, 2), (3, 4)), ((1, 3), (2, 4))), results=(week_one,), waiver_order=(4, 3, 2, 1),
        claims=(Claim(5, 1, 99, 13), Claim(6, 1, 98, None)), locked=(77, 78),
        lineup={"QB": (11,), "RB": (12, 13), "FLEX": ()},
        offers=(Offer(7, 2, 2, 1, (21,), (12, 13), "pending"),),
        events=(Event(1, 0, "draft", 3, "CPU Three took a quarterback", (31,)), Event(2, 1, "result", None, "Week 1")),
        traded={21: 1, 12: 2}, champion=None, next_id=8)
    return State(**{**fields, **overrides})


# ---------------- settings ----------------

def test_settings_defaults_describe_a_standard_twelve_team_league():
    settings = Settings(season=2021)

    assert (settings.teams, settings.scoring, settings.bench, settings.waiver_mode) == (12, "half", 6, "wednesday")
    assert settings.difficulty == "normal"
    assert dict(settings.starters) == {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1}
    assert settings.roster_size == 15
    assert settings.rounds == 15
    assert settings.end_week == 16


def test_settings_round_trip_through_json_exactly():
    settings = Settings(season=2019, teams=10, scoring="ppr", starters={"QB": 1, "RB": 2, "WR": 3, "TE": 1, "K": 1},
                        bench=5, draft_slot=7, waiver_mode="none", regular_season_weeks=13, playoff_teams=6,
                        trade_deadline_week=10, season_hidden=True, difficulty="hard")

    restored = rs.settings_from_json(rs.to_json(settings))

    assert restored == settings
    assert hash(restored) == hash(settings)
    assert restored.roster_size == 13


@pytest.mark.parametrize("bad", [
    {"scoring": "superflex"}, {"waiver_mode": "faab"}, {"teams": 11}, {"teams": 2}, {"draft_slot": 0},
    {"teams": 4}, {"teams": 6}, {"teams": 16}, {"difficulty": "easy"}, {"teams": 8, "draft_slot": 9},
    {"draft_slot": 13}, {"playoff_teams": 14}, {"starters": {"QB": 1, "IDP": 2}}, {"starters": {"QB": -1}},
    {"bench": -1}, {"regular_season_weeks": 0},
])
def test_settings_reject_bad_input_with_value_error(bad):
    with pytest.raises(ValueError):
        Settings(season=2021, **bad)


@pytest.mark.parametrize("teams", [8, 10, 12, 14])
def test_settings_accept_the_four_league_sizes(teams):
    settings = Settings(season=2021, teams=teams, draft_slot=teams, playoff_teams=teams)

    assert settings.teams == teams and rs.VALID_TEAMS == (8, 10, 12, 14)


def test_difficulty_sets_what_a_cpu_asks_of_the_user_and_its_draft_chaos():
    normal, hard = Settings(season=2021), Settings(season=2021, difficulty="hard")

    assert rs.DIFFICULTIES == ("normal", "hard")
    assert (rs.USER_TRADE_MULTIPLE[normal.difficulty], rs.USER_TRADE_MULTIPLE[hard.difficulty]) == (1.00, 1.08)
    assert (rs.DRAFT_CHAOS_SCALE[normal.difficulty], rs.DRAFT_CHAOS_SCALE[hard.difficulty]) == (1.0, 0.5)
    assert rs.settings_from_json(rs.to_json(hard)) == hard != normal


def test_settings_copy_the_starters_they_are_given():
    starters = {"QB": 1, "RB": 2}
    settings = Settings(season=2021, starters=starters)

    starters["RB"] = 9

    assert settings.starters["RB"] == 2
    with pytest.raises(TypeError):
        settings.starters["RB"] = 3


# ---------------- state ----------------

def test_state_round_trips_through_json_exactly():
    state = make_state()

    restored = rs.state_from_json(rs.to_json(state))

    assert restored == state
    assert hash(restored) == hash(state)                 # mappings are left out of the hash, so states hash
    assert rs.to_json(restored) == rs.to_json(state)


def test_round_trip_restores_tuples_int_keys_nested_records_and_none():
    restored = rs.state_from_json(rs.to_json(make_state()))

    result = restored.results[0]
    assert isinstance(restored.teams, tuple) and isinstance(restored.teams[0], Team)
    assert restored.schedule[1] == ((1, 3), (2, 4)) and isinstance(restored.schedule[1][0], tuple)
    assert result.scores[1] == 101.5 and set(result.scores) == {1, 2, 3, 4}
    assert result.starters[1]["RB"] == (12, 13) and result.starters[3] == {}
    assert result.points[12] == 0.1
    assert restored.traded == {21: 1, 12: 2} and all(isinstance(key, int) for key in restored.traded)
    assert restored.lineup["FLEX"] == ()
    assert restored.claims[1].drop is None and restored.events[1].team_id is None and restored.champion is None


def test_round_trip_keeps_a_missing_lineup_and_a_champion():
    state = make_state(lineup=None, champion=3, phase="complete")

    restored = rs.state_from_json(rs.to_json(state))

    assert restored == state
    assert restored.lineup is None and restored.champion == 3


def test_state_mappings_are_read_only_and_lists_become_tuples():
    state = make_state(locked=[5, 6], traded={1: 2})
    team = Team(9, "Listy", False, [1, 2], 1.0, {"QB": 1.0})

    assert state.locked == (5, 6) and team.roster == (1, 2)
    with pytest.raises(TypeError):
        state.traded[3] = 4
    with pytest.raises(TypeError):
        state.results[0].starters[1]["QB"] = (99,)
    with pytest.raises(TypeError):
        team.lean["QB"] = 2.0


def test_replacing_a_field_leaves_the_original_state_untouched():
    state = make_state()

    later = replace(state, week=3, traded={**state.traded, 31: 3})

    assert state.week == 2 and 31 not in state.traded
    assert later.traded[31] == 3 and later.teams is state.teams


def test_numpy_numbers_are_stored_as_plain_json_numbers():
    state = make_state(locked=(np.int64(5),), traded={np.int64(7): np.int32(2)},
                       results=(WeekResult(1, ((1, 2),), {1: np.float64(10.5), 2: 3.0}, {}, {}, np.bool_(False)),))

    restored = rs.state_from_json(rs.to_json(state))

    assert restored.locked == (5,) and type(restored.locked[0]) is int
    assert restored.traded == {7: 2} and restored.results[0].scores[1] == 10.5
    assert restored.results[0].playoff is False


def test_to_json_refuses_nan_and_unknown_types():
    with pytest.raises(ValueError):
        rs.to_json(make_state(results=(WeekResult(1, (), {1: float("nan")}, {}, {}),)))
    with pytest.raises(TypeError):
        rs.to_json(make_state(locked=({1, 2},)))


def test_from_json_reports_broken_text_as_value_error():
    with pytest.raises(ValueError):
        rs.state_from_json("{not json")
    with pytest.raises(ValueError):
        rs.state_from_json(json.dumps({"phase": "open", "week": 1, "teams": [{"id": 1}]}))


def test_from_json_ignores_unknown_keys_and_fills_defaults():
    saved = json.loads(rs.to_json(make_state()))
    saved.pop("next_id")
    saved["added_in_a_later_version"] = True

    restored = rs.state_from_json(json.dumps(saved))

    assert restored.next_id == 1 and restored.week == 2


def test_state_json_carries_its_version_and_another_version_is_refused():
    saved = json.loads(rs.to_json(make_state()))
    newer, unmarked = {**saved, "v": 2}, {key: value for key, value in saved.items() if key != "v"}

    assert saved["v"] == rs.STATE_VERSION == 1
    assert "v" not in json.loads(rs.to_json(Settings(season=2021)))
    with pytest.raises(ValueError, match="version 2"):
        rs.state_from_json(json.dumps(newer))
    with pytest.raises(ValueError, match="version None"):
        rs.state_from_json(json.dumps(unmarked))


def test_the_store_refuses_a_league_saved_in_another_version(engine):
    with Session(engine) as session:
        row = rs.create(session, "Old format", Settings(season=2021), make_state(), seed=1)
        row.state = json.dumps({**json.loads(row.state), "v": 0})
        session.commit()

        with pytest.raises(ValueError, match="version 0"):
            rs.load(session, row.id)


# ---------------- randomness ----------------

def test_rng_gives_the_same_numbers_for_the_same_seed_and_stream():
    first = rs.rng(42, "draft", 7).random(5)
    second = rs.rng(42, "draft", 7).random(5)

    assert first.tolist() == second.tolist()


def test_rng_is_stable_across_processes():
    # Captured in another interpreter run: a salted hash() would make these differ every time.
    assert rs.rng(7, "draft", 3).integers(0, 1_000_000, 3).tolist() == [169885, 759559, 783117]
    assert rs.rng(7).random() == pytest.approx(0.7415662261731492, abs=1e-15)


def test_rng_streams_are_independent():
    draws = {name: rs.rng(42, *stream).random() for name, stream in {
        "base": (), "draft": ("draft",), "draft7": ("draft", 7), "draft8": ("draft", 8), "7draft": (7, "draft"),
        "text7": ("draft", "7"), "waivers": ("waivers", 7)}.items()}
    other_seed = rs.rng(43, "draft", 7).random()

    assert len(set(draws.values())) == len(draws)
    assert other_seed not in draws.values()


def test_rng_accepts_numpy_ints_like_plain_ints_and_rejects_floats():
    assert rs.rng(np.int64(5), np.int32(2)).random() == rs.rng(5, 2).random()
    with pytest.raises(ValueError):
        rs.rng(5, 1.5)


# ---------------- helpers ----------------

def test_team_lookups_find_teams_and_reject_unknown_ids():
    state = make_state()

    assert rs.team(state, 2).name == "CPU Two"
    assert rs.user_team(state).id == 1
    assert rs.rostered(state) == frozenset({11, 12, 13, 21, 22, 31})
    with pytest.raises(ValueError):
        rs.team(state, 99)
    with pytest.raises(ValueError):
        rs.user_team(replace(state, teams=state.teams[1:]))


def test_with_team_replaces_one_team_and_keeps_the_rest():
    state = make_state()
    bigger = replace(rs.team(state, 4), roster=(41, 42))

    updated = rs.with_team(state, bigger)

    assert rs.team(updated, 4).roster == (41, 42)
    assert rs.team(state, 4).roster == ()
    assert updated.teams[:3] == state.teams[:3]
    with pytest.raises(ValueError):
        rs.with_team(state, replace(bigger, id=77))


def test_add_event_appends_with_the_next_id_and_current_week():
    state = make_state()

    updated = rs.add_event(state, "add", 1, "You added a kicker", [99])

    event = updated.events[-1]
    assert (event.id, event.week, event.kind, event.team_id, event.player_ids) == (8, 2, "add", 1, (99,))
    assert updated.next_id == 9 and len(state.events) == 2


def test_release_takes_a_player_off_the_roster_and_puts_him_on_waivers():
    state, settings = make_state(), Settings(season=2021)

    after = rs.release(state, settings, 1, 12, name="Some Back")

    assert rs.team(after, 1).roster == (11, 13) and rs.team(state, 1).roster == (11, 12, 13)
    assert after.locked == (77, 78, 12) and state.locked == (77, 78)
    event = after.events[-1]
    assert (event.kind, event.team_id, event.player_ids, event.week) == ("drop", 1, (12,), 2)
    assert event.text == "You dropped Some Back" and after.next_id == state.next_id + 1
    assert after.teams[1:] == state.teams[1:] and after.claims == state.claims


def test_release_without_logging_still_puts_him_on_waivers():
    state = make_state()

    after = rs.release(state, Settings(season=2021), 1, 12, log=False)

    assert rs.team(after, 1).roster == (11, 13) and after.locked == (77, 78, 12)
    assert after.events == state.events and after.next_id == state.next_id


def test_release_works_for_cpu_teams_without_a_name_and_never_locks_twice():
    state = make_state(locked=(21, 77))

    after = rs.release(state, Settings(season=2021, waiver_mode="none"), 2, 21)

    assert rs.team(after, 2).roster == (22,) and after.locked == (77, 21)       # locked in both waiver modes
    assert after.events[-1].text == "CPU Two dropped a player"


def test_release_rejects_a_player_who_is_not_on_that_roster():
    state, settings = make_state(), Settings(season=2021)

    with pytest.raises(ValueError, match="isn't on the roster"):
        rs.release(state, settings, 1, 21)
    with pytest.raises(ValueError):
        rs.release(state, settings, 99, 11)


def test_a_team_has_traded_this_week_only_with_an_accepted_offer_from_this_week():
    offers = (Offer(1, 2, 2, 1, (21,), (12,), "accepted"), Offer(2, 1, 3, 4, (31,), (), "accepted"),
              Offer(3, 2, 3, 4, (31,), (), "declined"), Offer(4, 2, 4, 3, (), (31,), "pending"))
    state = make_state(offers=offers)

    assert rs.has_traded_this_week(state, 1) and rs.has_traded_this_week(state, 2)      # either side
    assert not rs.has_traded_this_week(state, 3) and not rs.has_traded_this_week(state, 4)
    assert rs.has_traded_this_week(replace(state, week=1), 3) and not rs.has_traded_this_week(replace(state, week=3), 1)


def test_a_traded_player_is_locked_for_two_weeks():
    state = make_state(traded={21: 1, 12: 2})                 # the state is in week 2

    assert rs.RETRADE_LOCK_WEEKS == 2
    assert rs.retrade_locked(state, 12) and rs.retrade_locked(state, 21)
    assert not rs.retrade_locked(state, 11)                   # never traded
    assert rs.retrade_locked(replace(state, week=3), 12) and not rs.retrade_locked(replace(state, week=3), 21)
    assert not rs.retrade_locked(replace(state, week=4), 12)


# ---------------- the league schedule ----------------

@pytest.mark.parametrize("teams, weeks", [(8, 14), (10, 14), (12, 14), (14, 14), (12, 13), (8, 7), (14, 1), (4, 9)])
def test_round_robin_plays_every_team_once_a_week_and_spreads_opponents_evenly(teams, weeks):
    ids = list(range(1, teams + 1))

    schedule = rs.round_robin(ids, weeks, seed=11)

    meetings = Counter(frozenset(game) for week in schedule for game in week)
    assert len(schedule) == weeks
    assert all(sorted(team for game in week for team in game) == ids for week in schedule)
    assert all(len(pair) == 2 for pair in meetings)                               # nobody plays himself
    most, least = max(meetings.values()), min(meetings.values()) if len(meetings) == teams * (teams - 1) // 2 else 0
    assert most - least <= 1 and most == -(-weeks // (teams - 1))
    assert isinstance(schedule, tuple) and all(isinstance(game, tuple) for week in schedule for game in week)


def test_round_robin_is_set_by_the_seed_and_shares_home_games():
    ids = tuple(range(1, 13))

    first, again, other = (rs.round_robin(ids, 14, seed) for seed in (5, 5, 6))

    homes = Counter(home for week in first for home, _ in week)
    assert first == again and first != other
    assert all(3 <= homes[team] <= 11 for team in ids)
    assert rs.round_robin(ids, 14, 5)[:11] == rs.round_robin(ids, 11, 5)          # a longer season extends a shorter


def test_round_robin_fits_a_state_and_survives_json():
    state = make_state(schedule=rs.round_robin([1, 2, 3, 4], 5, seed=3))

    assert rs.state_from_json(rs.to_json(state)).schedule == state.schedule


@pytest.mark.parametrize("ids, weeks", [([1, 2, 3], 5), ([1], 5), ([], 5), ([1, 1, 2, 3], 5), ([1, 2], 0)])
def test_round_robin_rejects_odd_duplicate_or_empty_input(ids, weeks):
    with pytest.raises(ValueError):
        rs.round_robin(ids, weeks, seed=1)


def test_standings_break_equal_records_and_points_by_the_lower_team_id():
    level = WeekResult(1, ((4, 2), (3, 1)), {1: 80.0, 2: 80.0, 3: 80.0, 4: 80.0}, {}, {})

    table = rs.standings(make_state(results=(level,)))

    assert [row["team_id"] for row in table] == [1, 2, 3, 4] and all(row["ties"] == 1 for row in table)


def test_standings_count_regular_season_games_only_and_break_ties_on_points():
    week_two = WeekResult(2, ((1, 3), (2, 4)), {1: 90.0, 3: 95.5, 2: 120.0, 4: 60.0}, {}, {})
    semi_final = WeekResult(15, ((1, 4),), {1: 10.0, 4: 300.0}, {}, {}, playoff=True)
    state = make_state(results=(make_state().results[0], week_two, semi_final))

    table = rs.standings(state)

    by_id = {row["team_id"]: row for row in table}
    # 3 is 1-0-1 (a tie counts half); 2 and 1 are both 1-1, ordered by points for.
    assert [row["team_id"] for row in table] == [3, 2, 1, 4]
    assert [row["rank"] for row in table] == [1, 2, 3, 4]
    assert (by_id[1]["wins"], by_id[1]["losses"], by_id[1]["ties"]) == (1, 1, 0)
    assert (by_id[3]["wins"], by_id[3]["losses"], by_id[3]["ties"]) == (1, 0, 1)
    assert (by_id[4]["wins"], by_id[4]["losses"], by_id[4]["ties"]) == (0, 1, 1)
    assert by_id[1]["points_for"] == 191.5 and by_id[1]["points_against"] == 183.75
    assert by_id[4]["points_for"] == 130.0                           # the playoff blowout is not counted
    assert by_id[2]["name"] == "CPU Two"


def test_standings_before_any_game_list_every_team_at_zero():
    table = rs.standings(make_state(results=()))

    assert [row["team_id"] for row in table] == [1, 2, 3, 4]
    assert all(row["wins"] == row["losses"] == row["ties"] == 0 and row["points_for"] == 0.0 for row in table)


# ---------------- store ----------------

@pytest.fixture
def db(engine):
    with Session(engine) as session:
        yield session


def test_store_creates_and_loads_a_league_exactly(db):
    settings, state = Settings(season=2021, draft_slot=4), make_state()

    row = rs.create(db, "My replay", settings, state, seed=1234)
    loaded_row, loaded_settings, loaded_state = rs.load(db, row.id)

    assert (loaded_row.name, loaded_row.season, loaded_row.seed) == ("My replay", 2021, 1234)
    assert loaded_settings == settings and loaded_state == state


def test_store_save_overwrites_the_state_and_touches_updated_at(db):
    row = rs.create(db, "My replay", Settings(season=2021), make_state(), seed=1)
    before = row.updated_at

    rs.save(db, row, make_state(week=3, phase="waivers"))

    _, _, state = rs.load(db, row.id)
    assert (state.week, state.phase) == (3, "waivers")
    assert row.updated_at >= before


def test_store_lists_most_recently_played_first_and_deletes(db):
    old = rs.create(db, "Old", Settings(season=2019), make_state(), seed=1)
    new = rs.create(db, "New", Settings(season=2022), make_state(), seed=2)
    new.updated_at -= timedelta(minutes=1)               # the clock can tick too slowly to order two writes
    rs.save(db, old, make_state(week=5))

    assert [row.name for row in rs.list_leagues(db)] == ["Old", "New"]

    rs.delete(db, old.id)

    assert [row.id for row in rs.list_leagues(db)] == [new.id]


def test_store_raises_replay_not_found_for_a_missing_league(db):
    with pytest.raises(rs.ReplayNotFound):
        rs.load(db, 999)
    with pytest.raises(rs.ReplayNotFound):
        rs.delete(db, 999)
