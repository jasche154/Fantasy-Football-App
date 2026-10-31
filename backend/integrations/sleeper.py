"""Sleeper public API (no key): trending adds and drops, and live injury status.

Docs: https://docs.sleeper.com/ . Read-only, free for non-commercial use, stay under
1,000 calls/minute, and attribute Sleeper where the data is shown. Responses are cached
briefly to keep request volume low.
"""
import time
from threading import Lock

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import Player

BASE_URL = "https://api.sleeper.app/v1"
TIMEOUT_SECONDS = 10.0
CACHE_SECONDS = 15 * 60
TREND_TYPES = ("add", "drop")


class SleeperError(RuntimeError):
    pass


_cache: dict[tuple, tuple[float, list[dict]]] = {}
_lock = Lock()


def fetch_trending(trend_type: str, limit: int = 25, lookback_hours: int = 24,
                   client: httpx.Client | None = None) -> list[dict]:
    """Raw Sleeper rows: [{"player_id": "4046", "count": 1234}, ...]."""
    if trend_type not in TREND_TYPES:
        raise ValueError(f"trend_type must be one of {TREND_TYPES}")
    key = (trend_type, limit, lookback_hours)
    with _lock:
        cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]
    url = f"{BASE_URL}/players/nfl/trending/{trend_type}"
    params = {"lookback_hours": lookback_hours, "limit": limit}
    try:
        if client is None:
            with httpx.Client(timeout=TIMEOUT_SECONDS) as own_client:
                response = own_client.get(url, params=params)
        else:
            response = client.get(url, params=params)
        response.raise_for_status()
        rows = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SleeperError(f"Sleeper trending request failed: {exc}") from exc
    if not isinstance(rows, list):
        raise SleeperError("Unexpected Sleeper response shape.")
    with _lock:
        _cache[key] = (time.monotonic(), rows)
    return rows


def trending_players(session: Session, trend_type: str, limit: int = 10,
                     client: httpx.Client | None = None) -> list[dict]:
    """Trending rows matched to local players through players.sleeper_player_id."""
    rows = fetch_trending(trend_type, limit=limit, client=client)
    sleeper_ids = [str(r.get("player_id")) for r in rows]
    players = {p.sleeper_player_id: p for p in session.scalars(
        select(Player).where(Player.sleeper_player_id.in_(sleeper_ids)))}
    results = []
    for row in rows:
        sleeper_id = str(row.get("player_id"))
        player = players.get(sleeper_id)
        results.append({
            "player": ({"id": player.id, "name": player.full_name, "position": player.position,
                        "team": player.nfl_team} if player else None),
            "sleeper_id": sleeper_id, "count": int(row.get("count") or 0),
        })
    return results


PLAYERS_SOURCE = "sleeper_status"
PLAYERS_TIMEOUT_SECONDS = 60.0


def fetch_players(client: httpx.Client) -> dict:
    """The full NFL player map (~14 MB). Sleeper asks callers to fetch it at most daily-ish;
    the app refreshes it at most hourly and only while the Week X page is in use."""
    try:
        response = client.get(f"{BASE_URL}/players/nfl", timeout=PLAYERS_TIMEOUT_SECONDS)
        response.raise_for_status()
        players = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SleeperError(f"Sleeper players request failed: {exc}") from exc
    if not isinstance(players, dict):
        raise SleeperError("Unexpected Sleeper players response shape.")
    return players


def status_rows(players: dict, sleeper_to_player: dict[str, int]) -> list[dict]:
    """PlayerStatus rows for our QB/RB/WR/TE players."""
    rows = []
    for sleeper_id, item in players.items():
        player_id = sleeper_to_player.get(str(sleeper_id))
        if player_id is None or not isinstance(item, dict):
            continue
        order = item.get("depth_chart_order")
        news = item.get("news_updated")
        rows.append({"player_id": player_id, "injury_status": item.get("injury_status") or None,
                     "injury_body_part": item.get("injury_body_part") or None, "team": item.get("team"),
                     "depth_position": item.get("depth_chart_position"),
                     "depth_order": int(order) if isinstance(order, (int, float)) else None,
                     "injury_notes": str(item["injury_notes"])[:40] if item.get("injury_notes") else None,
                     "news_updated": int(news) if isinstance(news, (int, float)) else None})
    return rows


def refresh_status(session: Session, client: httpx.Client) -> int:
    """Replace the stored live statuses. Returns rows stored."""
    from sqlalchemy import delete, insert

    from database.models import PlayerStatus

    mapping = {sid: pid for pid, sid in session.execute(
        select(Player.id, Player.sleeper_player_id).where(Player.sleeper_player_id.is_not(None)))}
    rows = status_rows(fetch_players(client), mapping)
    if not rows:
        raise SleeperError("Sleeper returned no matching players.")
    session.execute(delete(PlayerStatus))
    session.execute(insert(PlayerStatus), rows)
    session.commit()
    return len(rows)
