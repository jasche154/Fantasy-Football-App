"""Player news (Sleeper) and comparing players over time."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.deps import league_params
from database.db import get_session
from database.models import Player
from integrations.sleeper import SleeperError
from integrations.sleeper_news import fetch_news
from services.compare import MAX_PLAYERS, MAX_SEASONS, compare_over_time
from services.league import LeagueSettings
from services.projections import available_seasons, latest_season

router = APIRouter(prefix="/api")


def _ints(text: str, name: str, limit: int) -> list[int]:
    try:
        values = list(dict.fromkeys(int(part) for part in text.split(",") if part.strip()))
    except ValueError as exc:
        raise HTTPException(422, f"{name} must be comma-separated numbers.") from exc
    if not values or len(values) > limit:
        raise HTTPException(422, f"Give 1 to {limit} {name}.")
    return values


@router.get("/players/{player_id}/news")
def player_news(player_id: int, session: Session = Depends(get_session)) -> dict:
    """Latest news for one player from Sleeper's partners (headline, summary, source link)."""
    player = session.get(Player, player_id)
    if player is None:
        raise HTTPException(404, "Player not found.")
    if not player.sleeper_player_id:
        return {"player_id": player_id, "items": [], "note": "No Sleeper id for this player yet."}
    try:
        return {"player_id": player_id, "items": fetch_news(player.sleeper_player_id), "source": "Sleeper"}
    except SleeperError as exc:
        raise HTTPException(502, "Sleeper news is unavailable right now.") from exc


@router.get("/compare")
def compare(ids: str = Query(..., max_length=60, description="Player ids, comma-separated (2-4)"),
            seasons: str | None = Query(None, max_length=40, description="Seasons, comma-separated (default: latest)"),
            league: LeagueSettings = Depends(league_params), session: Session = Depends(get_session)) -> dict:
    """Every regular-season game and a per-season summary for up to four players."""
    player_ids = _ints(ids, "players", MAX_PLAYERS)
    loaded = set(available_seasons(session))
    chosen = _ints(seasons, "seasons", MAX_SEASONS) if seasons else [latest_season(session)]
    missing = [s for s in chosen if s not in loaded]
    if missing:
        raise HTTPException(404, f"No data loaded for season(s) {missing}. Available: {sorted(loaded)}.")
    return compare_over_time(session, player_ids, chosen, league.scoring_settings())
