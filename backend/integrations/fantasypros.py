"""FantasyPros public API v2 client (needs FANTASYPROS_API_KEY).

Docs: https://api.fantasypros.com/public/v2/docs
Terms (https://api.fantasypros.com/public/v2/terms-of-use): personal, non-commercial use;
at most 1 call/second and 100 calls/day; attribution required; no storing historical
player statistics. This client only stores the latest ECR snapshot, replacing it on each
refresh, and refreshes at most every CACHE_HOURS. Every call is logged in
external_api_calls, so the daily cap and the failure back-off survive restarts.
"""
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from database.models import ExpertRanking, ExternalApiCall, Player

BASE_URL = "https://api.fantasypros.com/public/v2/json"
SOURCE = "fantasypros_api"
TIMEOUT_SECONDS = 15.0
MIN_SECONDS_BETWEEN_CALLS = 1.0
DAILY_CALL_LIMIT = 100
CACHE_HOURS = 6
FAILURE_BACKOFF_MINUTES = 30
RANKING_TYPES = ("ROS", "DRAFT", "WW")
SCORING_TYPES = ("PPR", "HALF", "STD")


class FantasyProsError(RuntimeError):
    pass


class _RateLimiter:
    """In-process guard for the 1-call-per-second limit (the daily cap is enforced in the DB)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._last_call = 0.0

    def acquire(self) -> None:
        with self._lock:
            wait = MIN_SECONDS_BETWEEN_CALLS - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()


rate_limiter = _RateLimiter()


def _utc(value: datetime | None) -> datetime | None:
    """SQLite drops tzinfo; stored times are UTC."""
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def calls_in_last_day(session: Session) -> int:
    since = datetime.now(timezone.utc) - timedelta(days=1)
    return session.scalar(select(func.count(ExternalApiCall.id)).where(
        ExternalApiCall.source == SOURCE, ExternalApiCall.called_at >= since)) or 0


def last_failure(session: Session) -> datetime | None:
    """Time of the latest call, if that call failed."""
    latest = session.execute(select(ExternalApiCall.called_at, ExternalApiCall.ok)
                             .where(ExternalApiCall.source == SOURCE)
                             .order_by(ExternalApiCall.called_at.desc()).limit(1)).first()
    return _utc(latest.called_at) if latest is not None and not latest.ok else None


def record_call(session: Session, ok: bool) -> None:
    session.add(ExternalApiCall(source=SOURCE, ok=ok))
    session.commit()


class FantasyProsClient:
    def __init__(self, api_key: str, http: httpx.Client | None = None):
        if not api_key:
            raise FantasyProsError("FANTASYPROS_API_KEY is not set in backend/.env.")
        self._api_key = api_key
        self._http = http

    def _get(self, path: str, params: dict) -> dict:
        rate_limiter.acquire()
        headers = {"x-api-key": self._api_key}
        try:
            if self._http is None:
                with httpx.Client(timeout=TIMEOUT_SECONDS) as http:
                    response = http.get(f"{BASE_URL}{path}", params=params, headers=headers)
            else:
                response = self._http.get(f"{BASE_URL}{path}", params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise FantasyProsError(f"FantasyPros request failed: {exc}") from exc
        if response.status_code in (401, 403):
            raise FantasyProsError("FantasyPros rejected the API key (check FANTASYPROS_API_KEY).")
        if response.status_code >= 400:
            raise FantasyProsError(f"FantasyPros returned HTTP {response.status_code}.")
        try:
            return response.json()
        except ValueError as exc:
            raise FantasyProsError("FantasyPros returned a non-JSON response.") from exc

    def consensus_rankings(self, season: int, position: str = "ALL", ranking_type: str = "ROS",
                           scoring: str = "PPR", week: int | None = None) -> dict:
        if ranking_type not in RANKING_TYPES or scoring not in SCORING_TYPES:
            raise ValueError(f"ranking_type must be in {RANKING_TYPES}, scoring in {SCORING_TYPES}")
        params = {"position": position, "type": ranking_type, "scoring": scoring}
        if week is not None:
            params["week"] = week
        return self._get(f"/nfl/{season}/consensus-rankings", params)


def _int(value) -> int | None:
    try:
        return int(float(value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _float(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def ranking_rows(payload: dict, season: int, ranking_type: str, scoring: str,
                 player_ids: dict[str, int]) -> list[dict]:
    rows = []
    for item in payload.get("players") or []:
        fantasypros_id = str(item.get("player_id")) if item.get("player_id") is not None else None
        rank = _int(item.get("rank_ecr"))
        if rank is None or not item.get("player_name"):
            continue
        rows.append({
            "player_id": player_ids.get(fantasypros_id), "fantasypros_id": fantasypros_id,
            "player_name": item["player_name"],
            "position": (item.get("player_positions") or item.get("player_position_id") or "").split(",")[0] or None,
            "team": item.get("player_team_id"), "source": SOURCE, "ranking_type": ranking_type.lower(),
            "scoring": scoring.lower(), "season": season, "week": _int(item.get("week")),
            "rank": rank, "ecr": _float(item.get("rank_ave")) or float(rank), "pos_rank": item.get("pos_rank"),
            "sd": _float(item.get("rank_std")), "best": _int(item.get("rank_min")),
            "worst": _int(item.get("rank_max")),
        })
    return rows


def last_refresh(session: Session, ranking_type: str, scoring: str) -> datetime | None:
    value = session.scalar(select(func.max(ExpertRanking.fetched_at)).where(
        ExpertRanking.source == SOURCE, ExpertRanking.ranking_type == ranking_type.lower(),
        ExpertRanking.scoring == scoring.lower()))
    return _utc(value)


def _check_quota(session: Session, now: datetime) -> None:
    failed_at = last_failure(session)
    if failed_at is not None and now - failed_at < timedelta(minutes=FAILURE_BACKOFF_MINUTES):
        minutes = int((now - failed_at).total_seconds() // 60)
        raise FantasyProsError(f"the last call failed {minutes} min ago; retrying after "
                               f"{FAILURE_BACKOFF_MINUTES} min")
    if calls_in_last_day(session) >= DAILY_CALL_LIMIT:
        raise FantasyProsError("daily call limit reached; try again tomorrow")


def refresh_rankings(session: Session, client: FantasyProsClient, season: int | None,
                     ranking_type: str = "ROS", scoring: str = "PPR", force: bool = False) -> int | None:
    """Replace the stored FantasyPros snapshot. Returns rows stored, or None if still fresh.

    Raises FantasyProsError (keeping the previous snapshot) when backing off after a
    failure, at the daily cap, on API errors, or when the response has no rankings.
    """
    if not season:
        raise FantasyProsError("no season loaded yet; run populate_stats first")
    now = datetime.now(timezone.utc)
    fetched = last_refresh(session, ranking_type, scoring)
    if not force and fetched and now - fetched < timedelta(hours=CACHE_HOURS):
        return None
    _check_quota(session, now)
    try:
        payload = client.consensus_rankings(season, "ALL", ranking_type, scoring)
    except FantasyProsError:
        record_call(session, ok=False)
        raise
    player_ids = {fp: pid for pid, fp in session.execute(
        select(Player.id, Player.fantasypros_id).where(Player.fantasypros_id.is_not(None)))}
    rows = ranking_rows(payload, season, ranking_type, scoring, player_ids)
    if not rows:
        record_call(session, ok=False)
        raise FantasyProsError("the response had no rankings; kept the previous snapshot")
    session.execute(delete(ExpertRanking).where(
        ExpertRanking.source == SOURCE, ExpertRanking.ranking_type == ranking_type.lower(),
        ExpertRanking.scoring == scoring.lower()))
    session.execute(insert(ExpertRanking), rows)
    record_call(session, ok=True)   # commits the new snapshot together with the call log
    return len(rows)
