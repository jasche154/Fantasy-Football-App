"""Week X rankings and opponent-adjusted strength of schedule."""
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from api.deps import Position, league_params, valuation_or_503
from config import get_settings
from database.db import get_session
from services import serialize
from services.league import LeagueSettings
from services.weekly_rankings import FIRST_WEEK, LAST_WEEK, build_weekly_rankings, default_week
from services.weekly_refresh import refresh_sources

router = APIRouter(prefix="/api")
Week = Query(None, ge=FIRST_WEEK, le=LAST_WEEK, description="NFL week (default: the current week)")


def _may_auto_refresh(request: Request, refresh: bool, week: int, current_week: int) -> bool:
    """Only the current week refreshes on read, and never for a cross-site page.

    A GET can't be blocked by the Origin check, so any website could otherwise make the
    browser trigger outside fetches (and Odds API spending) through this endpoint.
    """
    return (refresh and get_settings().weekly_auto_refresh and week == current_week
            and request.headers.get("sec-fetch-site") != "cross-site")


@router.get("/rankings/weekly")
def weekly_rankings(request: Request, week: int | None = Week, position: Position | None = None,
                    limit: int = Query(150, ge=1, le=500), refresh: bool = True,
                    league: LeagueSettings = Depends(league_params),
                    session: Session = Depends(get_session)) -> dict:
    """Blended projections for one week. For the current week, stale outside sources are
    refreshed first unless refresh=false (or WEEKLY_AUTO_REFRESH=false)."""
    valuation = valuation_or_503(session, league)
    current_week = default_week(valuation)
    week = week or current_week
    statuses = (refresh_sources(session, valuation.season, week)
                if _may_auto_refresh(request, refresh, week, current_week) else None)
    rankings = build_weekly_rankings(session, league, week)
    return serialize.weekly_rankings(rankings, position, limit, statuses)


@router.post("/rankings/weekly/refresh")
def weekly_refresh(week: int | None = Week, force: bool = False,
                   session: Session = Depends(get_session)) -> dict:
    """Re-fetch Yahoo and ESPN (force skips their caches). Props keep their credit budget:
    each game is bought once near kickoff; re-buying is only available from the CLI."""
    valuation = valuation_or_503(session, LeagueSettings())
    week = week or default_week(valuation)
    return {"season": valuation.season, "week": week,
            "sources": refresh_sources(session, valuation.season, week, force=force)}


@router.get("/schedule-strength")
def schedule_strength(position: Position = "WR", week: int | None = Week,
                      league: LeagueSettings = Depends(league_params),
                      session: Session = Depends(get_session)) -> dict:
    valuation = valuation_or_503(session, league)
    return serialize.schedule_strength(valuation, position, week or default_week(valuation))
