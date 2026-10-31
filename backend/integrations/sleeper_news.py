"""Player news through Sleeper (no key needed).

Sleeper's app reads https://api.sleeper.app/players/nfl/{sleeper_id}/news. It isn't in
Sleeper's documented API, so it may change without notice; like the Yahoo and ESPN feeds,
it's used for personal use, cached, and a failure only hides the news. The items come from
Sleeper's partners (FantasyPros, RotoWire): we show the headline, the short summary, the
source and a link to the full note, and never store them.
"""
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Lock

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import Player
from integrations.sleeper import SleeperError
from services.labels import untrusted

NEWS_URL = "https://api.sleeper.app/players/nfl/{sleeper_id}/news"
TIMEOUT_SECONDS = 10.0
CACHE_SECONDS = 30 * 60
MAX_ITEMS = 10
MAX_TEXT = 600
SOURCES = {"fantasy_pros": "FantasyPros", "rotowire": "RotoWire"}
SAFE_LINK_PREFIXES = ("https://",)
PARALLEL_REQUESTS = 8

_cache: dict[str, tuple[float, list[dict]]] = {}
_lock = Lock()


def _text(value: object) -> str | None:
    """News is outside text: one clean line, instruction-shaped sentences blanked (it can reach the AI)."""
    if not isinstance(value, str):
        return None
    return untrusted(value, MAX_TEXT) or None


def parse_news(rows: object) -> list[dict]:
    """Sleeper rows to {title, summary, analysis, source, url, published}, newest first."""
    items = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        title = _text(meta.get("title"))
        if not title:
            continue
        url = meta.get("url")
        published = row.get("published")
        try:                            # a huge or infinite stamp raises OSError/OverflowError on Windows
            when = (datetime.fromtimestamp(published / 1000, timezone.utc).isoformat()
                    if isinstance(published, (int, float)) and published > 0 else None)
        except (OverflowError, OSError, ValueError):
            when = None
        items.append({
            "title": title, "summary": _text(meta.get("description")), "analysis": _text(meta.get("analysis")),
            "source": SOURCES.get(row.get("source"), str(row.get("source") or "Sleeper")),
            "url": url if isinstance(url, str) and url.startswith(SAFE_LINK_PREFIXES) else None,
            "published": when,
        })
    items.sort(key=lambda i: i["published"] or "", reverse=True)
    return items[:MAX_ITEMS]


def fetch_news(sleeper_id: str, client: httpx.Client | None = None, fresh: bool = False) -> list[dict]:
    """fresh skips the 30-minute cache (the injury-news refresh needs the headline that changed the stamp)."""
    if not sleeper_id.isdigit() and not sleeper_id.isalpha():
        raise ValueError("Invalid Sleeper player id.")
    with _lock:
        cached = _cache.get(sleeper_id)
    if cached and not fresh and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]
    url = NEWS_URL.format(sleeper_id=sleeper_id)
    try:
        if client is None:
            with httpx.Client(timeout=TIMEOUT_SECONDS) as own_client:
                response = own_client.get(url, params={"limit": MAX_ITEMS}, follow_redirects=False)
        else:
            response = client.get(url, params={"limit": MAX_ITEMS}, follow_redirects=False)
        response.raise_for_status()
        items = parse_news(response.json())
    except (httpx.HTTPError, ValueError) as exc:
        raise SleeperError(f"Sleeper news request failed ({type(exc).__name__}).") from exc
    with _lock:
        _cache[sleeper_id] = (time.monotonic(), items)
    return items


def news_by_player(session: Session, player_ids: Iterable[int],
                   client: httpx.Client | None = None) -> dict[int, list[dict]]:
    """{player_id: newest-first items} for players with a Sleeper id; fetched in parallel, failures skipped."""
    rows = session.execute(select(Player.id, Player.full_name, Player.sleeper_player_id)
                           .where(Player.id.in_(list(player_ids)), Player.sleeper_player_id.is_not(None))).all()

    def one(row) -> tuple[int, list[dict]]:
        player_id, name, sleeper_id = row
        try:
            news = fetch_news(sleeper_id, client)
        except (SleeperError, ValueError):
            news = []                                  # one player's feed failing never hides the rest
        return player_id, [{**item, "player": {"id": player_id, "name": name}} for item in news]

    with ThreadPoolExecutor(max_workers=PARALLEL_REQUESTS) as pool:
        return {pid: items for pid, items in pool.map(one, rows) if items}


def news_for_players(session: Session, player_ids: Iterable[int], client: httpx.Client | None = None,
                     limit: int = MAX_ITEMS) -> list[dict]:
    """Newest items across several players, each tagged with its player."""
    items = [item for news in news_by_player(session, player_ids, client).values() for item in news]
    items.sort(key=lambda i: i["published"] or "", reverse=True)
    return items[:limit]
