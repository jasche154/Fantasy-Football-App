"""Week X rankings and opponent-adjusted strength of schedule."""
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from api.deps import Position, league_params, valuation_or_503
from config import get_settings
from database.db import get_session
from fastapi import HTTPException

from services import serialize
from services.flags import player_flags
from services.league import LeagueSettings
from services.valuation import Valuation
from services.waivers import DEFAULT_MAX_ROSTERED, waiver_candidates
from services.weekly_rankings import FIRST_WEEK, LAST_WEEK, WeeklyRankings, build_weekly_rankings, default_week
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
                    ids: str | None = Query(None, max_length=80, description="Comma-separated ids (start/sit)"),
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
    wanted = _parse_ids(ids)
    return serialize.weekly_rankings(rankings, position, limit, statuses, flags_for(rankings, valuation), wanted)


def _parse_ids(ids: str | None) -> list[int] | None:
    if not ids:
        return None
    try:
        parsed = [int(part) for part in ids.split(",") if part.strip()]
    except ValueError as exc:
        raise HTTPException(422, "ids must be comma-separated player ids") from exc
    if len(parsed) > 8:
        raise HTTPException(422, "compare at most 8 players")
    return parsed


def flags_for(rankings: WeeklyRankings, valuation: Valuation) -> dict[int, tuple]:
    seasons = {p.player_id: p for p in valuation.seasons}
    if rankings.context is None:
        return {}
    return {p.player_id: player_flags(seasons[p.player_id], rankings.context)
            for p in rankings.players if p.player_id in seasons}


@router.get("/rankings/weekly/{player_id}/explain")
def weekly_explain(player_id: int, week: int | None = Week, league: LeagueSettings = Depends(league_params),
                   session: Session = Depends(get_session)) -> dict:
    """Step-by-step reasoning behind one player's Week X projection."""
    valuation = valuation_or_503(session, league)
    rankings = build_weekly_rankings(session, league, week or default_week(valuation))
    player = next((p for p in rankings.players if p.player_id == player_id), None)
    if player is None:
        raise HTTPException(404, "That player has no projection this week (bye, ruled out, or not covered).")
    flags = flags_for(rankings, valuation).get(player_id, ())
    return {"season": rankings.season, "week": rankings.week, **serialize.explanation(player, flags)}


@router.get("/insights/flags")
def flags(week: int | None = Week, position: Position | None = None,
          kind: str | None = Query(None, pattern="^(breakout|regression|risk)$"),
          limit: int = Query(40, ge=1, le=200), league: LeagueSettings = Depends(league_params),
          session: Session = Depends(get_session)) -> dict:
    """Breakout, regression and injury-risk flags for players projected this week."""
    valuation = valuation_or_503(session, league)
    rankings = build_weekly_rankings(session, league, week or default_week(valuation))
    by_player = flags_for(rankings, valuation)
    results = []
    for player in rankings.players:
        chosen = [f for f in by_player.get(player.player_id, ()) if kind is None or f.kind == kind]
        if chosen and (position is None or player.position == position):
            results.append(serialize.weekly_player(player, chosen))
    return {"season": rankings.season, "week": rankings.week, "results": results[:limit]}


@router.get("/waivers")
def waivers(week: int | None = Week, position: Position | None = None,
            max_rostered: float = Query(DEFAULT_MAX_ROSTERED, ge=0, le=100), limit: int = Query(25, ge=1, le=100),
            league: LeagueSettings = Depends(league_params), session: Session = Depends(get_session)) -> dict:
    """Waiver targets (ESPN roster rate at most max_rostered) with a FAAB suggestion."""
    valuation = valuation_or_503(session, league)
    rankings = build_weekly_rankings(session, league, week or default_week(valuation))
    candidates = waiver_candidates(rankings, valuation, position, max_rostered, limit)
    return {"season": rankings.season, "week": rankings.week, "max_rostered": max_rostered,
            "results": [serialize.waiver(c) for c in candidates]}


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
