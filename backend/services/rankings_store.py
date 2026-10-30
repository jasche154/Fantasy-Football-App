"""Local persistence for weekly rankings: a snapshot cache and a projection history.

- Snapshots: the first build of a week is slow (sources, injuries, the model); after that the
  result is read back from SQLite, including after a restart. The key covers league settings,
  week, every data input and a hash of the model code, so stale results are never served.
- History: the last pre-kickoff projection per player-week, with each source's points, so
  `python -m services.projection_eval` can score them against actual results.

Both tables live in the local database (gitignored). Failures here never break rankings:
they are logged and the caller falls back to computing.

Snapshots are pickled, so each is signed (HMAC-SHA256) with a key kept in a local file
(backend/.snapshot_key, gitignored, made on first use). A snapshot that doesn't verify,
for example from a tampered database or an edited backup, is ignored instead of unpickled.
"""
import hashlib
import hmac
import json
import logging
import pickle
import secrets
from collections.abc import Iterable
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database.models import Base, ProjectionHistory, RankingsSnapshot, utc_now

log = logging.getLogger(__name__)
SERVICES_DIR = Path(__file__).parent
# Changing any of these changes the numbers, so it invalidates saved snapshots.
MODEL_FILES = ("weekly_rankings.py", "weekly_adjust.py", "weekly_model.py", "injuries.py", "flags.py",
               "valuation.py", "projections.py", "ros_consensus.py", "schedule_strength.py",
               "projection_ranges.json", "score_ranges.py", "score_ranges.json")
SNAPSHOT_TTL = timedelta(days=21)
KEY_FILE = SERVICES_DIR.parent / ".snapshot_key"
SIGNATURE_BYTES = 32
TABLES = (RankingsSnapshot.__table__, ProjectionHistory.__table__)
UNREADABLE = (SQLAlchemyError, pickle.UnpicklingError, AttributeError, ImportError, EOFError, TypeError)


@lru_cache(maxsize=1)
def model_version() -> str:
    digest = hashlib.sha256()
    for name in MODEL_FILES:
        path = SERVICES_DIR / name
        if path.exists():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def cache_key(league: object, week: int, version: Iterable[object]) -> str:
    parts = [repr(league), str(week), model_version(), *(str(v) for v in version)]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


_ready: set[str] = set()


def _ensure_tables(session: Session) -> None:
    """Create the tables on first use (for databases that predate the migration)."""
    url = str(session.get_bind().url)
    if url not in _ready:
        Base.metadata.create_all(session.get_bind(), tables=list(TABLES))
        _ready.add(url)


@lru_cache(maxsize=1)
def _signing_key() -> bytes:
    if not KEY_FILE.exists():
        KEY_FILE.write_bytes(secrets.token_bytes(32))
    return KEY_FILE.read_bytes()


def _sign(payload: bytes) -> bytes:
    return hmac.new(_signing_key(), payload, hashlib.sha256).digest() + payload


def _verified(blob: bytes) -> bytes | None:
    signature, payload = blob[:SIGNATURE_BYTES], blob[SIGNATURE_BYTES:]
    expected = hmac.new(_signing_key(), payload, hashlib.sha256).digest()
    return payload if hmac.compare_digest(signature, expected) else None


def load(session: Session, key: str) -> object | None:
    try:
        _ensure_tables(session)
        row = session.get(RankingsSnapshot, key)
        payload = _verified(row.payload) if row else None
        if row and payload is None:
            log.warning("Ignoring a rankings snapshot that failed its signature check")
        return pickle.loads(payload) if payload else None     # signed by save() below
    except UNREADABLE:
        log.warning("Ignoring an unreadable rankings snapshot", exc_info=True)
        session.rollback()
        return None


def save(session: Session, key: str, season: int, week: int, rankings: object) -> None:
    """Store the snapshot (replacing this key) and drop any past the TTL.

    Other snapshots for the same week stay: each league setting (a Yahoo league's scoring,
    the default PPR) has its own, and switching between them shouldn't force a rebuild.
    """
    try:
        _ensure_tables(session)
        session.execute(delete(RankingsSnapshot).where(
            (RankingsSnapshot.computed_at < utc_now() - SNAPSHOT_TTL) | (RankingsSnapshot.cache_key == key)))
        session.add(RankingsSnapshot(cache_key=key, season=season, week=week,
                                     payload=_sign(pickle.dumps(rankings, protocol=pickle.HIGHEST_PROTOCOL))))
        session.commit()
    except SQLAlchemyError:
        log.warning("Could not save a rankings snapshot", exc_info=True)
        session.rollback()


def record_history(session: Session, rankings) -> int:
    """Upsert pre-kickoff projections; games already final keep their last pre-game row."""
    try:
        _ensure_tables(session)
        now = utc_now()
        rows = [dict(season=rankings.season, week=rankings.week, scoring=rankings.scoring,
                     player_id=player.player_id, position=player.position, projection=player.projection,
                     if_active=player.if_active, play_prob=player.play_prob, floor=player.floor,
                     ceiling=player.ceiling, computed_at=now,
                     sources=json.dumps({name: round(v.points, 2) for name, v in player.sources.items()}))
                for player in rankings.players if not player.matchup.game_final]
        if rows:                                       # one upsert instead of a SELECT + write per player
            statement = sqlite_insert(ProjectionHistory)
            session.execute(statement.on_conflict_do_update(
                index_elements=["season", "week", "scoring", "player_id"],
                set_={c: statement.excluded[c] for c in ("position", "projection", "if_active", "play_prob",
                                                          "floor", "ceiling", "sources", "computed_at")}), rows)
        session.commit()
        return len(rows)
    except SQLAlchemyError:
        log.warning("Could not record projection history", exc_info=True)
        session.rollback()
        return 0


def history(session: Session, season: int, scoring: str) -> list[ProjectionHistory]:
    _ensure_tables(session)
    return list(session.scalars(select(ProjectionHistory).where(
        ProjectionHistory.season == season, ProjectionHistory.scoring == scoring)))
