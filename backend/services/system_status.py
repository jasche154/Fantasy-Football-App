"""What the app's background work has been doing, kept between restarts (the app_state table).

Recorded here:
- auto_update: when the background check last ran, what it did, and any error (type only);
- odds_credits: Odds API credits left this month, from the last props purchase;
- backup: the newest database backup (services/backup.py).

status() gathers these with the data freshness for GET /api/system/status.
"""
import json
import logging
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database.models import AppState, Base, DataLoad, LearnedWeights, utc_now

log = logging.getLogger(__name__)
KEYS = ("auto_update", "odds_credits", "backup")


def _ensure_table(session: Session) -> None:
    Base.metadata.create_all(session.get_bind(), tables=[AppState.__table__])


def record(session: Session, key: str, value: dict) -> None:
    """Save one value (best-effort: status bookkeeping never breaks the work it describes)."""
    try:
        _ensure_table(session)
        session.merge(AppState(key=key, value=json.dumps(value, default=str), updated_at=utc_now()))
        session.commit()
    except SQLAlchemyError:
        log.warning("Could not record %s status", key, exc_info=True)
        session.rollback()


def read(session: Session, key: str) -> dict | None:
    try:
        _ensure_table(session)
        row = session.get(AppState, key)
    except SQLAlchemyError:
        session.rollback()
        return None
    if row is None:
        return None
    try:
        value = json.loads(row.value)
    except ValueError:
        return None
    return {**value, "updated_at": row.updated_at.isoformat()} if isinstance(value, dict) else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def status(session: Session) -> dict:
    from config import get_settings
    from services.projections import latest_season, latest_week

    settings = get_settings()
    season = latest_season(session)
    last_load = session.scalar(select(DataLoad).order_by(DataLoad.id.desc()).limit(1))
    learned = session.scalar(select(func.max(LearnedWeights.week)).where(LearnedWeights.season == season)) \
        if season else None
    return {
        "auto_update": {"enabled": settings.auto_update, **(read(session, "auto_update") or {})},
        "odds_credits": read(session, "odds_credits"),
        "backup": {"enabled": settings.backups, "keep": settings.backup_keep, **(read(session, "backup") or {})},
        "data": {"season": season, "latest_week": latest_week(session, season) if season else None,
                 "last_load": _iso(getattr(last_load, "finished_at", None)),
                 "weights_learned_through_week": learned},
        "auth_required": bool(settings.api_token),
    }
