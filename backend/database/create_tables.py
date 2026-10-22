"""Create the schema for a brand-new database.

Run from backend/:  python -m database.create_tables

A fresh database gets every table from the models and is stamped at the latest Alembic
revision. An existing database is left alone; upgrade it with `alembic upgrade head`.
"""
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from config import BACKEND_DIR
from database.db import engine
from database.models import Base


def main() -> None:
    if inspect(engine).has_table("alembic_version"):
        print("Database already exists; run `alembic upgrade head` to apply new migrations.")
        return
    Base.metadata.create_all(engine)
    alembic_config = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    alembic_config.attributes["database_url"] = engine.url.render_as_string(hide_password=False)
    command.stamp(alembic_config, "head")
    print("Tables created.")


if __name__ == "__main__":
    main()
