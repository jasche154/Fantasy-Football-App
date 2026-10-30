"""Keep the data and the learned weights current while the app runs, without manual steps.

Every CHECK_HOURS (first check shortly after startup) a background thread:
1. **Loads new stats** when a week has finished: its last game was at least STATS_LAG_DAYS
   ago (nflverse posts box scores overnight) and that week isn't loaded yet. This is the
   same load as `python -m database.populate_stats`, which also runs the weekly
   source-weight update (services/weight_learning.py).
2. **Re-imports your Yahoo leagues** (rosters, lineups, rules) when Yahoo is connected,
   so My Team, league waivers and league trades follow adds, drops and trades.
3. **Backs up the database** once a day (services/backup.py).
4. **Saves the pre-game snapshot** for the current week while it has games left: refreshes
   the outside sources (same caches and Odds API credit budget as opening the Week X page),
   then builds the default league's rankings, which records each player's pre-kickoff
   projection in projection_history. That's what the next weight update learns from, so
   learning keeps going even in weeks you never open the Week X page.

Every PROPS_TICK_MINUTES on game days it also buys Vegas props for games kicking off within
two hours (integrations/odds_api.py).

Turn it off with AUTO_UPDATE=false in backend/.env. It only runs while the API is running.
"""
import logging
import threading
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy.orm import Session, sessionmaker

from services.schedule_strength import Schedule

log = logging.getLogger(__name__)

CHECK_HOURS = 6
PROPS_TICK_MINUTES = 15     # game days: props are bought within 2 hours of each kickoff
FIRST_CHECK_SECONDS = 90       # let startup finish (and the value cache warm) first
STATS_LAG_DAYS = 1
_started = threading.Event()


@dataclass
class UpdateResult:
    loaded_week: int | None = None           # stats loaded for this finished week
    synced_leagues: int = 0                  # Yahoo leagues re-imported
    snapshot_week: int | None = None         # pre-game projections saved for this week
    notes: list[str] = field(default_factory=list)


def finished_week_to_load(schedule: Schedule, latest_loaded: int | None, today: date) -> int | None:
    """The newest week whose games all ended STATS_LAG_DAYS+ ago and isn't loaded yet."""
    cutoff = (today - timedelta(days=STATS_LAG_DAYS)).isoformat()
    days: dict[int, list[str | None]] = defaultdict(list)
    for game in schedule.games:
        days[game.week].append(game.gameday)
    done = [week for week, dates in days.items() if dates and all(d is not None and d <= cutoff for d in dates)]
    newest = max(done, default=None)
    return newest if newest is not None and newest > (latest_loaded or 0) else None


def run_once(session_factory: Callable[[], Session], today: date | None = None,
             load_stats: Callable[[list[int]], None] | None = None,
             refresh: Callable[[Session, int, int], object] | None = None,
             sync_leagues: Callable[[Session], int] | None = None) -> UpdateResult:
    """One check. The loaders are injectable so tests don't touch the network."""
    from config import get_settings
    from database.populate_stats import load_data
    from services.league import LeagueSettings
    from services.projections import latest_season, latest_week
    from services.schedule_strength import load_schedule
    from services.valuation import build_valuation
    from services.weekly_rankings import build_weekly_rankings, default_week
    from services.weekly_refresh import refresh_sources

    today = today or date.today()
    load_stats = load_stats or (lambda seasons: load_data(seasons))
    refresh = refresh or refresh_sources
    result = UpdateResult()
    with session_factory() as session:
        season = latest_season(session)
        if season is None:
            result.notes.append("no stats loaded yet; run populate_stats once")
            return result
        due = finished_week_to_load(load_schedule(session, season), latest_week(session, season), today)
    if due is not None:
        load_stats([season])                   # also nudges the source weights (weight_learning)
        result.loaded_week = due
    with session_factory() as session:
        result.synced_leagues = (sync_leagues or sync_yahoo_leagues)(session)
    with session_factory() as session:
        league = LeagueSettings()
        week = default_week(build_valuation(session, league))
        games = [g for g in load_schedule(session, season).games if g.week == week]
        if any(not g.is_final for g in games):
            if get_settings().weekly_auto_refresh:
                refresh(session, season, week)  # same caches and props credit budget as the page
            build_weekly_rankings(session, league, week)      # records pre-kickoff projections
            result.snapshot_week = week
    return result


def sync_yahoo_leagues(session: Session) -> int:
    """Re-import every league that has the user's team (best-effort; returns how many synced)."""
    import httpx

    from config import get_settings
    from services import yahoo_sync
    from services.my_team import my_leagues

    if yahoo_sync.get_connection(session) is None:
        return 0
    synced = 0
    with httpx.Client(timeout=15.0) as http:
        for league, _ in my_leagues(session):
            try:
                yahoo_sync.import_league(session, get_settings(), http, league.platform_league_id)
                synced += 1
            except Exception:          # Yahoo down or the token revoked: keep the last import
                log.warning("Could not re-import Yahoo league %s", league.name, exc_info=True)
                session.rollback()
    return synced


def check(session_factory: Callable[[], Session]) -> dict:
    """One full check (stats, Yahoo, snapshot, backup), recorded for the status page."""
    from datetime import datetime, timezone

    from services import backup, system_status

    started = datetime.now(timezone.utc)
    status: dict = {"at": started.isoformat(), "ok": True}
    try:
        result = run_once(session_factory)
        status.update(loaded_week=result.loaded_week, synced_leagues=result.synced_leagues,
                      snapshot_week=result.snapshot_week, notes=result.notes)
        if result.loaded_week or result.snapshot_week or result.synced_leagues:
            log.info("Auto-update: loaded week %s stats, synced %s Yahoo leagues, saved week %s projections",
                     result.loaded_week, result.synced_leagues, result.snapshot_week)
    except Exception as exc:      # never let a bad check kill the thread; try again next time
        log.exception("Auto-update check failed")
        status.update(ok=False, error=type(exc).__name__)    # the type only: messages can carry URLs
    with session_factory() as session:
        backup.run(session)
        status["seconds"] = round((datetime.now(timezone.utc) - started).total_seconds(), 1)
        status["next_check_hours"] = CHECK_HOURS
        system_status.record(session, "auto_update", status)
    return status


def props_due_soon(schedule: Schedule, now) -> int | None:
    """The week of any unplayed game kicking off within the props window, else None."""
    from datetime import timedelta

    from integrations.odds_api import PROPS_WINDOW_HOURS

    for game in schedule.games:
        kickoff = game.kickoff
        if not game.is_final and kickoff and now < kickoff <= now + timedelta(hours=PROPS_WINDOW_HOURS):
            return game.week
    return None


def props_tick(session_factory: Callable[[], Session]) -> dict | None:
    """Game days: buy props for games that just entered the window before kickoff."""
    from datetime import datetime, timezone

    from services.projections import latest_season
    from services.schedule_strength import load_schedule
    from services.weekly_refresh import refresh_props_only

    with session_factory() as session:
        season = latest_season(session)
        week = props_due_soon(load_schedule(session, season), datetime.now(timezone.utc)) if season else None
        return refresh_props_only(session, season, week) if week else None


def _loop(session_factory: Callable[[], Session]) -> None:
    import time

    wait, next_check = FIRST_CHECK_SECONDS, time.monotonic() + FIRST_CHECK_SECONDS
    while True:
        if _stop.wait(min(wait, PROPS_TICK_MINUTES * 60)):
            return
        try:
            props_tick(session_factory)
        except Exception:          # a props failure must not stop the stats/Yahoo checks
            log.exception("Game-day props check failed")
        if time.monotonic() < next_check:
            continue
        try:
            check(session_factory)
        except Exception:          # status bookkeeping or the backup failed; keep the thread alive
            log.exception("Auto-update bookkeeping failed")
        wait, next_check = CHECK_HOURS * 3600, time.monotonic() + CHECK_HOURS * 3600


_stop = threading.Event()


def start(session_factory: sessionmaker) -> bool:
    """Start the background thread once per process; False if it's already running."""
    if _started.is_set():
        return False
    _started.set()
    threading.Thread(target=_loop, args=(session_factory,), name="auto-update", daemon=True).start()
    return True
