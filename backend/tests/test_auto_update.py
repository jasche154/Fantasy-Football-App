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
                                  refresh=lambda s, season, week: refreshes.append((season, week)))
    # Week 7's game (Oct 17) is 1+ days past but its stats aren't loaded (latest week is 6).
    assert loads == [[SEASON]] and result.loaded_week == WEEK
    assert refreshes == [(SEASON, WEEK)] and result.snapshot_week == WEEK
    saved = session.scalar(select(func.count()).select_from(ProjectionHistory).where(ProjectionHistory.week == WEEK))
    assert saved > 0


def test_run_once_skips_the_snapshot_when_the_week_is_over_and_stops_without_data(engine):
    result = auto_update.run_once(sessionmaker(bind=engine), today=date(2026, 10, 30),
                                  load_stats=lambda seasons: None, refresh=lambda *a: None)
    assert result.loaded_week is None and result.snapshot_week is None and result.notes


def test_start_runs_one_thread_per_process(monkeypatch):
    started = []
    monkeypatch.setattr(auto_update, "_started", type(auto_update._started)())
    monkeypatch.setattr(auto_update.threading, "Thread",
                        lambda **kw: type("T", (), {"start": lambda self: started.append(kw["name"])})())
    assert auto_update.start(None) is True
    assert auto_update.start(None) is False
    assert started == ["auto-update"]
