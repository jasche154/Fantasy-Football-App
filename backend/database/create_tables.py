"""Create a new database or bring an existing one up to the latest schema.

Run from backend/:  python -m database.create_tables

- Brand-new database: every table is created from the models and stamped at the latest
  Alembic revision.
- Database already tracked by Alembic: pending migrations are applied.
- Database built by older scripts with Base.metadata.create_all() and never stamped: every
  migration is applied from the start. They are all guarded, so steps that already exist
  are skipped.
"""
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from config import BACKEND_DIR
from database.db import engine
from database.models import Base


def alembic_config(target: Engine) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.attributes["database_url"] = target.url.render_as_string(hide_password=False)
    return config


def ensure_schema(target: Engine | None = None) -> str:
    """Create or upgrade the schema; returns "created", "upgraded", or "upgraded-legacy"."""
    target = target or engine
    inspector = inspect(target)
    config = alembic_config(target)
    if inspector.has_table("alembic_version"):
        command.upgrade(config, "head")
        return "upgraded"
    if inspector.has_table("players"):
        command.upgrade(config, "head")
        return "upgraded-legacy"
    Base.metadata.create_all(target)
    command.stamp(config, "head")
    return "created"


def main() -> None:
    outcome = ensure_schema()
    print({"created": "Tables created.",
           "upgraded": "Database is on the latest schema.",
           "upgraded-legacy": "Existing untracked database upgraded to the latest schema."}[outcome])


if __name__ == "__main__":
    main()
