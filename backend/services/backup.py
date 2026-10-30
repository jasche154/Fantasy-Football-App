"""Copies of the local SQLite database: projection history and learned weights can't be re-downloaded.

The auto-update thread makes one a day (BACKUPS=true, the default) and keeps the newest
BACKUP_KEEP. By hand, from backend/:
    python -m services.backup

Copies use SQLite's online backup API, so they're consistent while the app is running.
Restore by stopping the API and copying a backup over backend/database/fantasy.db.
"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import logging  # noqa: E402
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

from sqlalchemy.engine import make_url

log = logging.getLogger(__name__)
PREFIX = "fantasy-"
BACKUP_EVERY = timedelta(hours=24)


def sqlite_path(database_url: str) -> Path | None:
    """The file behind a SQLite URL; None for in-memory or other databases."""
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return None
    return Path(url.database)


def backups(directory: Path) -> list[Path]:
    """Existing backups, oldest first."""
    return sorted(directory.glob(f"{PREFIX}*.db")) if directory.is_dir() else []


def backup_database(database_url: str, directory: Path, keep: int, now: datetime | None = None) -> Path:
    """Write a new backup and delete all but the newest `keep`. Returns the new file."""
    source = sqlite_path(database_url)
    if source is None or not source.exists():
        raise ValueError("Backups only cover a local SQLite database file.")
    now = now or datetime.now(timezone.utc)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{PREFIX}{now:%Y%m%d-%H%M%S}.db"
    # closing(): a sqlite3 connection's own `with` only commits, and an open handle locks the file on Windows.
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
    for old in backups(directory)[:-max(keep, 1)]:
        old.unlink()
    return target


def due(last: dict | None, now: datetime | None = None) -> bool:
    if not last or not last.get("at"):
        return True
    try:
        made = datetime.fromisoformat(last["at"])
    except (TypeError, ValueError):
        return True
    return (now or datetime.now(timezone.utc)) - made >= BACKUP_EVERY


def backup_now(session) -> dict:
    """Make a backup now and record the result for the status page (failures included)."""
    from config import get_settings
    from services import system_status

    settings = get_settings()
    try:
        path = backup_database(settings.database_url, Path(settings.backup_dir), settings.backup_keep)
    except (OSError, sqlite3.Error, ValueError) as exc:
        log.warning("Database backup failed: %s", type(exc).__name__)
        result = {"at": datetime.now(timezone.utc).isoformat(), "ok": False, "error": type(exc).__name__}
    else:
        result = {"at": datetime.now(timezone.utc).isoformat(), "ok": True, "file": path.name,
                  "size_mb": round(path.stat().st_size / 1e6, 1), "count": len(backups(path.parent))}
    system_status.record(session, "backup", result)
    return result


def run(session) -> dict | None:
    """One backup if the last is a day old; None when not due or disabled."""
    from config import get_settings
    from services import system_status

    if not get_settings().backups or not due(system_status.read(session, "backup")):
        return None
    return backup_now(session)


def main() -> None:
    from database.db import SessionLocal

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with SessionLocal() as session:
        result = backup_now(session)
    if not result["ok"]:
        raise SystemExit(f"Backup failed ({result['error']}).")
    log.info("Backed up to %s (%.1f MB)", result["file"], result["size_mb"])


if __name__ == "__main__":
    main()
