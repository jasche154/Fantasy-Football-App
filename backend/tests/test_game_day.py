"""Game-day behavior: kickoff times, locks, late FLEX, warnings, sit risk, props timing, pooled-budget rules."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from integrations import odds_api
from services import auto_update, game_locks
from services.injuries import Availability, usage_availability
from services.schedule_strength import Game, Schedule


def _game(week, home, away, day, time, final=False):
    return Game(week, home, away, 20 if final else None, 17 if final else None, None, None, day, time)


def test_kickoff_converts_eastern_time_across_daylight_saving():
    assert _game(9, "A", "B", "2026-11-08", "13:00").kickoff == datetime(2026, 11, 8, 18, 0, tzinfo=timezone.utc)
    assert _game(5, "A", "B", "2026-10-04", "13:00").kickoff == datetime(2026, 10, 4, 17, 0, tzinfo=timezone.utc)
    assert _game(5, "A", "B", None, "13:00").kickoff is None
    assert game_locks.eastern_time(datetime(2026, 11, 8, 21, 25, tzinfo=timezone.utc)) == "Sun 4:25 PM ET"


def _p(pid, position, projection=10.0, play=1.0, actual=None, team="T", status=None):
    return SimpleNamespace(player_id=pid, position=position, projection=projection, play_prob=play, actual=actual,
                           team=team, name=f"P{pid}", status=status)


def test_locks_keep_started_players_and_late_flex_swaps_unlocked_starters():
    now = datetime(2026, 11, 8, 19, 0, tzinfo=timezone.utc)
    kickoff = {1: now - timedelta(hours=1), 2: now + timedelta(hours=2), 3: now + timedelta(hours=5)}
    players = [_p(1, "RB"), _p(2, "RB"), _p(3, "RB"), _p(4, "WR", actual=12.0)]
    assert game_locks.locked_ids(players, kickoff, now) == {1, 4}
    lineup = {"RB": [2, 3], "FLEX": [1]}
    assert game_locks.prefer_late_flex(lineup, {1: "RB", 2: "RB", 3: "RB"}, kickoff, {1}) == lineup   # FLEX locked
    early_flex = {"RB": [3, 1], "FLEX": [2]}
    swapped = game_locks.prefer_late_flex(early_flex, {1: "RB", 2: "RB", 3: "RB"}, kickoff, {1})
    assert swapped == {"RB": [2, 1], "FLEX": [3]}                         # latest kickoff (3) moves into FLEX
    key = game_locks.lock_key({9})
    assert key(_p(9, "RB", 1.0)) > key(_p(8, "RB", 30.0))


def test_warnings_name_the_inactives_time_and_whether_a_backup_plays_later():
    now = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
    kickoff = {1: datetime(2026, 11, 8, 21, 25, tzinfo=timezone.utc), 2: datetime(2026, 11, 8, 18, 0, tzinfo=timezone.utc)}
    risky, backup = _p(1, "RB", play=0.7, status="Questionable"), _p(2, "RB", projection=8.0)
    (note,) = game_locks.alerts([risky], [backup], kickoff, set())
    assert "70% to play" in note and "inactives about Sun 2:55 PM ET" in note and "plays earlier" in note
    assert game_locks.alerts([_p(3, "RB")], [], {3: kickoff[1]}, set()) == []        # healthy: no warning
    assert now < kickoff[2]


def test_untagged_players_with_low_snaps_or_inactive_last_week_carry_sit_risk():
    games = (_game(6, "T", "V", "2026-10-18", "13:00", final=True), _game(7, "T", "U", "2026-10-25", "13:00", final=True))
    schedule = Schedule(games=games, opponents={("T", 6): "V", ("V", 6): "T", ("T", 7): "U", ("U", 7): "T"})
    opp = {6: "V", 7: "U"}
    line = lambda week, snaps: SimpleNamespace(week=week, snap_pct=snaps, opponent=opp[week])  # noqa: E731
    seasons = [SimpleNamespace(player_id=1, team="T", position="RB", games=(line(6, 60), line(7, 8.0))),
               SimpleNamespace(player_id=2, team="T", position="WR", games=(line(6, 70),)),          # sat week 7
               SimpleNamespace(player_id=3, team="T", position="WR", games=(line(7, 90.0),)),        # every-down
               SimpleNamespace(player_id=4, team="T", position="RB", games=(line(7, 5.0),))]         # already tagged
    tagged = {4: Availability(4, 0.5, "Questionable", "LP", None, "report")}
    out = usage_availability(seasons, tagged, 8, schedule)
    assert out[1].play_prob == 0.76 and out[1].status == "Low snaps"
    assert out[2].play_prob == 0.34 and out[2].label.startswith("Check inactives")
    assert 3 not in out and 4 not in out


def test_props_are_bought_close_to_kickoff_and_the_tick_finds_games_in_the_window():
    assert odds_api.PROPS_WINDOW_HOURS == 2
    now = datetime(2026, 11, 8, 16, 0, tzinfo=timezone.utc)
    schedule = Schedule(games=(_game(9, "A", "B", "2026-11-08", "13:00"), _game(9, "C", "D", "2026-11-08", "16:25")))
    assert auto_update.props_due_soon(schedule, now) == 9                    # 1 p.m. ET = 18:00 UTC, 2 h away
    assert auto_update.props_due_soon(schedule, now - timedelta(hours=6)) is None
    thursday = SimpleNamespace(gameday="2026-11-05")
    sunday = SimpleNamespace(gameday="2026-11-08")
    assert odds_api._always_buy(thursday) and not odds_api._always_buy(sunday)
