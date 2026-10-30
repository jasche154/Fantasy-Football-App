"""Read-only data endpoints: meta, players, rankings, leaderboards, expert rankings."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.deps import Position, league_params, valuation_or_503
from api.integration_routes import integration_status
from config import get_settings
from database.db import get_session
from integrations.fantasypros import FantasyProsClient, FantasyProsError, refresh_rankings
from services import serialize
from services.experts import expert_rankings, weekly_ecr_by_player
from services.leaderboards import catalog, leaderboard
from services.league import POSITIONS, LeagueSettings
from services.players import PlayerNotFound, player_profile, search_players
from services.projections import available_seasons, latest_season, latest_week
from services.schedule_strength import load_schedule
from services.scoring import POINTS_PER_RECEPTION
from services.valuation import NoDataError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


def _current_week(session: Session, season: int | None, latest: int | None) -> int | None:
    """The week Week X rankings default to: the first with games left (same rule as weekly_rankings)."""
    if not season:
        return None
    week = load_schedule(session, season).current_week() or (latest or 0) + 1
    return min(max(week, 1), 18)


@router.get("/meta")
def meta(session: Session = Depends(get_session)) -> dict:
    season = latest_season(session)
    week = latest_week(session, season) if season else None
    return {
        "current_season": season, "latest_week": week, "current_week": _current_week(session, season, week),
        "seasons": available_seasons(session), "positions": list(POSITIONS),
        "scoring_presets": list(POINTS_PER_RECEPTION), "integrations": integration_status(session),
        "auth_required": bool(get_settings().api_token),
    }


@router.get("/players/search")
def players_search(q: str = Query(..., min_length=1, max_length=60), position: Position | None = None,
                   limit: int = Query(10, ge=1, le=25), session: Session = Depends(get_session)) -> dict:
    return {"results": [serialize.player_ref(r) for r in search_players(session, q, position, limit)]}


@router.get("/players/{player_id}")
def player_detail(player_id: int, season: int | None = Query(None, ge=1999, le=2100),
                  league: LeagueSettings = Depends(league_params),
                  session: Session = Depends(get_session)) -> dict:
    if season is not None and season not in available_seasons(session):
        raise HTTPException(404, f"No data loaded for season {season}.")
    try:
        return player_profile(session, player_id, league, season)
    except PlayerNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except NoDataError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/rankings")
def rankings(position: Position | None = None, limit: int = Query(100, ge=1, le=500),
             league: LeagueSettings = Depends(league_params), session: Session = Depends(get_session)) -> dict:
    valuation = valuation_or_503(session, league)
    values = [v for v in valuation.values if position is None or v.position == position][:limit]
    return serialize.rankings(valuation, values, weekly_ecr_by_player(session))


@router.get("/leaderboards/metrics")
def leaderboard_metrics() -> dict:
    return {"categories": catalog()}


@router.get("/leaderboards")
def leaderboards(category: str, metric: str, season: int | None = Query(None, ge=1999, le=2100),
                 min_volume: int | None = Query(None, ge=0, le=5000), limit: int = Query(25, ge=1, le=200),
                 ascending: bool = False, position: Position | None = None,
                 session: Session = Depends(get_session)) -> dict:
    season = season or latest_season(session)
    try:
        rows = leaderboard(session, category, metric, season, min_volume, limit, ascending, position)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    definition = next(m for c in catalog() if c["key"] == category for m in c["metrics"] if m["key"] == metric)
    return {"category": category, "metric": metric, "label": definition["label"], "season": season,
            "min_volume": definition["default_min_volume"] if min_volume is None else min_volume,
            "results": [serialize.leaderboard_row(r) for r in rows]}


@router.get("/experts/rankings")
def experts(position: Position | None = None, limit: int = Query(50, ge=1, le=500),
            session: Session = Depends(get_session)) -> dict:
    """FantasyPros ROS ECR when a key is configured (refreshed at most every few hours),
    otherwise the free weekly ECR snapshot loaded by populate_stats."""
    warning = None
    api_key = get_settings().fantasypros_api_key
    if api_key:
        try:
            refresh_rankings(session, FantasyProsClient(api_key), latest_season(session))
        except FantasyProsError as exc:
            log.warning("FantasyPros refresh failed: %s", exc)
            warning = f"FantasyPros refresh failed ({exc}); showing the last stored rankings."
    return {**expert_rankings(session, position, limit), "warning": warning}
