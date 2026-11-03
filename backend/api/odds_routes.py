"""The Odds page: stored lines and player props. Never calls The Odds API."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database.db import get_session
from services.odds_board import LAST_WEEK, default_week, latest_schedule_season, odds_board

router = APIRouter(prefix="/api")


@router.get("/odds")
def odds(season: int | None = Query(None, ge=1999, le=2100),
         week: int | None = Query(None, ge=1, le=LAST_WEEK, description="NFL week (default: the current week)"),
         session: Session = Depends(get_session)) -> dict:
    """A week's spreads, totals and implied team totals, plus the player props already stored."""
    season = season or latest_schedule_season(session)
    if season is None:
        raise HTTPException(503, "No NFL schedule is loaded yet. Run populate_stats first.")
    return odds_board(session, season, week or default_week(session, season))
