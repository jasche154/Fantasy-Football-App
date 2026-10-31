"""Refresh the outside weekly sources (Yahoo experts, ESPN, Vegas props) for one week.

Run from backend/ (e.g. on a schedule, or before setting lineups):
    python -m services.weekly_refresh                # current week, only stale sources
    python -m services.weekly_refresh --week 8 --force
    python -m services.weekly_refresh --force-props  # re-buy props already bought (costs credits)

The Week X rankings endpoint also calls `refresh_sources` for stale sources, so opening the
page keeps it current. Each source fails on its own: any error keeps that source's last
snapshot and is reported back instead of failing the whole refresh. Only one refresh runs
at a time; a request that arrives meanwhile reads the stored snapshot.
"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse  # noqa: E402
import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from config import Settings, get_settings
from integrations import espn, odds_api, sleeper, yahoo_experts
from integrations.weekly_sources import (
    FAILURE_BACKOFF_MINUTES, TIMEOUT_SECONDS, SourceError, in_backoff, last_call, last_fetched, record_call,
)

log = logging.getLogger(__name__)

CACHE_HOURS = 3          # Yahoo and ESPN; props have their own longer cache (credits are scarce)
FREE_SOURCES: dict[str, Callable[[Session, httpx.Client, int, int], int]] = {
    yahoo_experts.SOURCE: yahoo_experts.refresh,
    espn.SOURCE: espn.refresh,
}
ALL_SOURCES = (sleeper.PLAYERS_SOURCE, *FREE_SOURCES, odds_api.SOURCE)
LABELS = {yahoo_experts.SOURCE: "Yahoo", espn.SOURCE: "ESPN", odds_api.SOURCE: "The Odds API",
          sleeper.PLAYERS_SOURCE: "Sleeper"}
STATUS_CACHE_HOURS = 1       # live injury designations
_refresh_lock = threading.Lock()


def _refresh_weather(session: Session, http: httpx.Client, season: int, week: int, force: bool) -> dict:
    """Kickoff forecasts for upcoming outdoor games (free, best-effort)."""
    from services.weather import refresh_forecasts

    try:
        stored = refresh_forecasts(session, http, season, week, force)
    except Exception:          # weather is optional; never fail the refresh over it
        log.warning("Weather refresh failed", exc_info=True)
        session.rollback()
        return _status("error", "Weather forecast unavailable; projections use no weather adjustment.")
    return _status("updated" if stored else "fresh", rows=stored)


def _is_fresh(session: Session, season: int, week: int, source: str) -> bool:
    fetched = last_fetched(session, season, week, source)
    return fetched is not None and datetime.now(timezone.utc) - fetched < timedelta(hours=CACHE_HOURS)


def _disabled() -> dict:
    return _status("disabled", "Turned off in WEEKLY_SOURCES.")


def _status(state: str, message: str | None = None, rows: int | None = None) -> dict:
    return {"state": state, "rows": rows, "message": message}


def _refresh_free(session: Session, http: httpx.Client, source: str, season: int, week: int,
                  force: bool) -> dict:
    if not force and _is_fresh(session, season, week, source):
        return _status("fresh")
    if in_backoff(session, source):
        return _status("error", f"The last {LABELS[source]} refresh failed; retrying after "
                                f"{FAILURE_BACKOFF_MINUTES} min. Showing the last stored data.")
    try:
        rows = FREE_SOURCES[source](session, http, season, week)
    except Exception:
        session.rollback()
        record_call(session, source, ok=False)
        raise
    record_call(session, source, ok=True)
    return _status("updated", rows=rows)


def _refresh_status(session: Session, http: httpx.Client, force: bool) -> dict:
    """Sleeper live injury status (one 14 MB call, at most hourly)."""
    latest = last_call(session, sleeper.PLAYERS_SOURCE)
    if not force and latest and latest[1] and datetime.now(timezone.utc) - latest[0] < timedelta(hours=STATUS_CACHE_HOURS):
        return _status("fresh")
    if in_backoff(session, sleeper.PLAYERS_SOURCE):
        return _status("error", f"The last Sleeper refresh failed; retrying after {FAILURE_BACKOFF_MINUTES} min.")
    try:
        rows = sleeper.refresh_status(session, http)
    except sleeper.SleeperError as exc:
        session.rollback()
        record_call(session, sleeper.PLAYERS_SOURCE, ok=False)
        raise SourceError(str(exc)) from exc
    record_call(session, sleeper.PLAYERS_SOURCE, ok=True)
    from services import injury_news

    news = injury_news.refresh(session, http)      # per-player failures are counted, not raised
    return _status("updated", f"Injury news re-read for {news['fetched']} players.", rows=rows)


def _props_budget_teams(session: Session, season: int) -> set[str] | None:
    """None when credits cover every game left this month; else the NFL teams on your rosters."""
    from database.models import Player
    from services import system_status
    from services.my_team import my_leagues, rosters

    credits = (system_status.read(session, "odds_credits") or {}).get("remaining")
    if credits is None or credits >= odds_api.credits_needed(session, season):
        return None
    mine = {s.player_id for league, team in my_leagues(session) for s in rosters(session, league).get(team.id, [])}
    teams = set(session.scalars(select(Player.nfl_team).where(Player.id.in_(mine), Player.nfl_team.is_not(None))))
    return teams or None


def refresh_props_only(session: Session, season: int, week: int) -> dict:
    """The game-day props check (every 15 minutes from the auto-update): buys games that just entered
    the window before kickoff, nothing else."""
    settings = get_settings()
    if not settings.odds_api_key or "vegas_props" not in settings.weekly_sources:
        return _disabled()
    with httpx.Client(timeout=TIMEOUT_SECONDS) as http:
        try:
            return _refresh_props(session, http, settings, season, week, False)
        except SourceError as exc:
            return _status("error", str(exc))


def _refresh_props(session: Session, http: httpx.Client, settings: Settings, season: int,
                   week: int, force_props: bool) -> dict:
    if not settings.odds_api_key:
        return _status("not_configured", "Add ODDS_API_KEY to backend/.env to include Vegas player props.")
    client = odds_api.OddsApiClient(settings.odds_api_key, http)
    bought = odds_api.refresh(session, client, season, week, force_props, _props_budget_teams(session, season))
    if bought == 0:
        return _status("fresh", f"Each game's props are bought once, within {odds_api.PROPS_WINDOW_HOURS} h "
                                "of kickoff; nothing is due right now.")
    if client.remaining is not None:
        from services import system_status
        system_status.record(session, "odds_credits", {"remaining": client.remaining})
    left = f" ({client.remaining} credits left this month)" if client.remaining is not None else ""
    return _status("updated", f"Bought props for {bought} games{left}.", rows=bought)


def _refresh_one(session: Session, http: httpx.Client, settings: Settings, source: str, season: int,
                 week: int, force: bool, force_props: bool) -> dict:
    """One source's refresh; every failure becomes an "error" status (the last snapshot stays)."""
    if source not in settings.weekly_sources:
        return _disabled()
    try:
        if source == sleeper.PLAYERS_SOURCE:
            return _refresh_status(session, http, force)
        if source == odds_api.SOURCE:
            return _refresh_props(session, http, settings, season, week, force_props)
        return _refresh_free(session, http, source, season, week, force)
    except SourceError as exc:
        session.rollback()
        log.warning("%s refresh failed: %s", source, exc)
        return _status("error", str(exc))
    except Exception:                               # unofficial sources can change shape any day
        session.rollback()
        log.exception("%s refresh failed unexpectedly", source)
        return _status("error", f"{LABELS[source]} returned data this app couldn't read; kept the last snapshot.")


PROPS_CHECK_HOURS = 2         # how often a page view may look for props that came due


def _stale_sources(session: Session, settings: Settings, season: int, week: int) -> list[str]:
    """Sources a refresh would actually touch right now (cheap: database reads only)."""
    now = datetime.now(timezone.utc)
    stale = []
    for source in settings.weekly_sources:
        if source in FREE_SOURCES:
            if not _is_fresh(session, season, week, source) and not in_backoff(session, source):
                stale.append(source)
        elif source == sleeper.PLAYERS_SOURCE:
            latest = last_call(session, source)
            if not (latest and latest[1] and now - latest[0] < timedelta(hours=STATUS_CACHE_HOURS)):
                stale.append(source)
        elif source == odds_api.SOURCE and settings.odds_api_key:
            latest = last_call(session, odds_api.CALL_LOG_SOURCE)
            if latest is None or now - latest[0] >= timedelta(hours=PROPS_CHECK_HOURS):
                stale.append(source)
    return stale


def _run_in_background(bind, season: int, week: int) -> None:
    try:
        with Session(bind) as session:
            refresh_sources(session, season, week)
    except Exception:                                   # never let a background thread die silently
        log.exception("background refresh failed")


def start_background_refresh(session: Session, season: int, week: int,
                             settings: Settings | None = None) -> dict[str, dict] | None:
    """Kick off a refresh of stale sources without making the page wait.

    Returns {source: "refreshing" status} for what was started, None when nothing is stale
    or a refresh is already running.
    """
    settings = settings or get_settings()
    stale = _stale_sources(session, settings, season, week)
    if not stale or _refresh_lock.locked():
        return None
    threading.Thread(target=_run_in_background, args=(session.get_bind(), season, week),
                     name="weekly-refresh", daemon=True).start()
    return {source: _status("refreshing", "Updating in the background; reload in a moment.") for source in stale}


def refresh_sources(session: Session, season: int, week: int, settings: Settings | None = None,
                    force: bool = False, http: httpx.Client | None = None,
                    force_props: bool = False) -> dict[str, dict]:
    """{source: {"state": updated|fresh|not_configured|disabled|busy|error, "rows", "message"}}.

    Runs in the caller's thread; page requests use start_background_refresh instead.

    `force` re-fetches the free sources (Yahoo, ESPN). Props follow their credit budget
    unless `force_props` re-buys games already bought.
    """
    if not _refresh_lock.acquire(blocking=False):
        return {s: _status("busy", "Another refresh is running; showing stored data.") for s in ALL_SOURCES}
    settings = settings or get_settings()
    owns_client = http is None
    http = http or httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=True)
    try:
        statuses = {source: _refresh_one(session, http, settings, source, season, week, force, force_props)
                    for source in ALL_SOURCES}
        statuses["weather"] = _refresh_weather(session, http, season, week, force)
        return statuses
    finally:
        if owns_client:
            http.close()
        _refresh_lock.release()


def main(argv: list[str] | None = None) -> None:
    from database.db import engine
    from services.league import LeagueSettings
    from services.valuation import build_valuation
    from services.weekly_rankings import default_week

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # httpx logs every request URL at INFO, and The Odds API takes its key as a query parameter.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--week", type=int, help="week to refresh (default: the current week)")
    parser.add_argument("--force", action="store_true", help="re-fetch Yahoo and ESPN even if fresh")
    parser.add_argument("--force-props", action="store_true",
                        help="re-buy Vegas props for games already bought (costs Odds API credits)")
    args = parser.parse_args(argv)
    with Session(engine) as session:
        valuation = build_valuation(session, LeagueSettings())
        week = args.week or default_week(valuation)
        statuses = refresh_sources(session, valuation.season, week, force=args.force, force_props=args.force_props)
        for source, status in statuses.items():
            log.info("%s (week %s): %s%s%s", source, week, status["state"],
                     f", {status['rows']} rows" if status["rows"] else "",
                     f" - {status['message']}" if status["message"] else "")


if __name__ == "__main__":
    main()
