from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from config import get_settings

DB_PATH = Path(__file__).resolve().parent / "fantasy.db"


SQLITE_BUSY_TIMEOUT_SECONDS = 30


def make_engine(url: str) -> Engine:
    is_sqlite = url.startswith("sqlite")
    new_engine = create_engine(url, connect_args={"timeout": SQLITE_BUSY_TIMEOUT_SECONDS} if is_sqlite else {})
    if new_engine.dialect.name == "sqlite":
        @event.listens_for(new_engine, "connect")
        def _configure_sqlite(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            # SQLite ships with foreign keys off; turn them on for every connection.
            cursor.execute("PRAGMA foreign_keys=ON")
            # WAL lets page reads run while a source refresh writes (the default journal makes
            # long reads block writers, which surfaced as "database is locked").
            if url not in ("sqlite://", "sqlite:///:memory:"):
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return new_engine


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    with SessionLocal() as session:
        yield session
