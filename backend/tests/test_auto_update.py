"""The background weekly update: load finished weeks, save pre-game projections."""
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from database.models import NflGame, ProjectionHistory
from services import auto_update
from services import weekly_rankings as weekly
from services.schedule_strength import Game, Schedule
from tests.test_injury_model import SEASON, WEEK, _seed_week


def _schedule(days_by_week):
    games = [Game(week=w, home_team="AAA", away_team="BBB", home_score=None, away_score=None,
                  spread_line=None, total_line=None, gameday=d)
             for w, days in days_by_week.items() for d in days]
    return Schedule(games=games, opponents={})


def test_a_week_is_due_a_day_after_its_last_game_until_it_is_loaded():
    schedule = _schedule({7: ["2026-10-22", "2026-10-25", "2026-10-26"], 8: ["2026-10-29", "2026-11-01"]})
    assert auto_update.finished_week_to_load(schedule, 6, date(2026, 10, 26)) is None     # Monday night today
    assert auto_update.finished_week_to_load(schedule, 6, date(2026, 10, 27)) == 7
    assert auto_update.finished_week_to_load(schedule, 7, date(2026, 10, 28)) is None     # already loaded
    assert auto_update.finished_week_to_load(_schedule({7: [None]}), 6, date(2026, 12, 1)) is None


def test_run_once_loads_the_finished_week_and_saves_the_current_weeks_projections(engine, seeded, session, monkeypatch):
    from config import get_settings

    monkeypatch.setenv("WEEKLY_AUTO_REFRESH", "true")          # the refresh itself is a stub below
    get_settings.cache_clear()
    _seed_week(session)                                          # weeks 1-6 played, week 7 upcoming
    for game in session.scalars(select(NflGame)):
        game.gameday = f"2026-10-{game.week + 10:02d}"
    session.commit()
    weekly._cache.clear()
    loads, refreshes = [], []
    factory = sessionmaker(bind=engine)
    result = auto_update.run_once(factory, today=date(2026, 10, 30), load_stats=loads.append,
                                  sync_leagues=lambda s, season: (2, 0), season_now=lambda: SEASON,
                                  refresh=lambda s, season, week: refreshes.append((season, week)))
    # Week 7's game (Oct 17) is 1+ days past but its stats aren't loaded (latest week is 6), so a load runs.
    # The fake load adds nothing: the week isn't claimed as loaded, and the other steps still run.
    assert loads == [[SEASON]] and result.loaded_week is None and result.synced_leagues == 2
    assert result.ok is False and "week 7 stats weren't available yet" in result.notes[0]
    assert refreshes == [(SEASON, WEEK)] and result.snapshot_week == WEEK
    saved = session.scalar(select(func.count()).select_from(ProjectionHistory).where(ProjectionHistory.week == WEEK))
    assert saved > 0


def test_a_week_counts_as_loaded_only_once_its_stats_are_in(engine, monkeypatch):
    from services import projections

    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(projections, "latest_week", lambda session, season: 7)
    result = auto_update.UpdateResult()
    auto_update._load_stats(factory, lambda seasons: None, SEASON, 7, result)
    assert result.loaded_week == 7 and result.ok

    def broken(seasons):
        raise TimeoutError("crosswalk")

    result = auto_update.UpdateResult()
    auto_update._load_stats(factory, broken, SEASON, 8, result)
    assert result.loaded_week is None and result.ok is False and "TimeoutError" in result.notes[0]


def test_a_new_season_loads_its_schedule_and_one_failing_step_doesnt_stop_the_rest(engine, seeded):
    loads = []

    def yahoo_down(session, season):
        raise ConnectionError("yahoo")

    result = auto_update.run_once(sessionmaker(bind=engine), today=date(2027, 9, 10), load_stats=loads.append,
                                  refresh=lambda *a: None, sync_leagues=yahoo_down, season_now=lambda: SEASON + 1)
    assert loads == [[SEASON + 1]]          # newest stats are last season's; the new season is fetched
    assert result.ok is False and any("Yahoo re-sync failed (ConnectionError)" in n for n in result.notes)
    assert any(f"the {SEASON + 1} schedule isn't available yet" in n for n in result.notes)
    assert result.snapshot_week is None     # no new-season stats yet: last season's week 18 isn't rebuilt


def test_failed_yahoo_leagues_are_reported(engine, seeded):
    result = auto_update.run_once(sessionmaker(bind=engine), today=date(2026, 10, 30), load_stats=lambda s: None,
                                  refresh=lambda *a: None, sync_leagues=lambda s, season: (1, 2),
                                  season_now=lambda: SEASON)
    assert result.failed_leagues == 2 and result.ok is False
    assert any("2 Yahoo league(s)" in n for n in result.notes)


def test_run_once_skips_the_snapshot_when_the_week_is_over_and_stops_without_data(engine):
    result = auto_update.run_once(sessionmaker(bind=engine), today=date(2026, 10, 30),
                                  load_stats=lambda seasons: None, refresh=lambda *a: None,
                                  sync_leagues=lambda s, season: (0, 0), season_now=lambda: SEASON)
    assert result.loaded_week is None and result.snapshot_week is None and result.notes


def test_start_runs_one_thread_per_process(monkeypatch):
    started = []
    monkeypatch.setattr(auto_update, "_started", type(auto_update._started)())
    monkeypatch.setattr(auto_update.threading, "Thread",
                        lambda **kw: type("T", (), {"start": lambda self: started.append(kw["name"])})())
    assert auto_update.start(None) is True
    assert auto_update.start(None) is False
    assert started == ["auto-update"]


def test_yahoo_sync_skips_without_a_connection(session):
    assert auto_update.sync_yahoo_leagues(session) == (0, 0)


def test_yahoo_resync_skips_old_seasons_and_counts_failures(session, monkeypatch):
    from types import SimpleNamespace

    from services import my_team, yahoo_sync

    leagues = [SimpleNamespace(name=f"L{s}", season=s, platform_league_id=f"key{s}")
               for s in (SEASON - 1, SEASON, SEASON + 1, 0)]          # 0 = imported without a season
    imported = []

    def fake_import(session, settings, http, key):
        if key == f"key{SEASON + 1}":
            raise RuntimeError("token revoked")
        imported.append(key)

    monkeypatch.setattr(yahoo_sync, "get_connection", lambda session: object())
    monkeypatch.setattr(my_team, "my_leagues", lambda session: [(league, None) for league in leagues])
    monkeypatch.setattr(yahoo_sync, "import_league", fake_import)
    assert auto_update.sync_yahoo_leagues(session, SEASON) == (2, 1)        # last season's league skipped
    assert imported == [f"key{SEASON}", "key0"]
    imported.clear()
    assert auto_update.sync_yahoo_leagues(session) == (3, 1)                # no season: every league
