import polars as pl
import pytest
from sqlalchemy.orm import Session

from database import populate_stats as populate
from database.models import Player, PlayerExpectedPointsWeekly, PlayerStatsWeekly


@pytest.fixture
def db(engine):
    with Session(engine) as session:
        yield session


def stats_frame(rows: list[dict]) -> pl.DataFrame:
    base = {"player_id": "00-1", "player_display_name": "New Guy", "position": "WR", "team": "AAA",
            "season": 2026, "week": 1, "season_type": "REG", "receptions": 5, "unrelated_column": 1}
    return pl.DataFrame([{**base, **row} for row in rows])


def test_id_helpers():
    assert populate.as_int("3.0") == 3 and populate.as_int(None) is None
    assert populate.as_id(15514.0) == "15514" and populate.as_id("abc") == "abc" and populate.as_id(None) is None


def test_player_index_creates_players_once_and_tracks_latest_team(db):
    index = populate.PlayerIndex(db)
    first = index.resolve("00-1", "New Guy", "WR", "AAA")
    assert index.resolve("00-1", "New Guy", "WR", "BBB") == first
    assert index.resolve(None, "No Id", "WR", "AAA") is None
    index.note_team(first, 2026, 5, "BBB", "WR")
    index.note_team(first, 2026, 2, "AAA", "WR")         # older row doesn't win
    populate.apply_latest_teams(db, index)
    assert db.get(Player, first).nfl_team == "BBB"


def test_load_feed_maps_matching_columns_and_replaces_season(db):
    frames = {2026: stats_frame([{"week": 1}, {"week": 2.0}, {"player_id": None}])}
    feed = populate.Feed("test", PlayerStatsWeekly, lambda seasons: frames[seasons[0]],
                         "player_id", "player_display_name", "position", "team")
    index = populate.PlayerIndex(db)
    populate.load_feed(db, feed, [2026], index)
    populate.load_feed(db, feed, [2026], index)          # reload doesn't duplicate rows
    rows = db.query(PlayerStatsWeekly).order_by(PlayerStatsWeekly.week).all()
    assert [r.week for r in rows] == [1, 2] and rows[0].receptions == 5


def test_failed_download_keeps_existing_rows(db):
    good = populate.Feed("ok", PlayerExpectedPointsWeekly,
                         lambda s: pl.DataFrame([{"player_id": "00-1", "full_name": "A", "position": "RB",
                                                  "posteam": "X", "season": "2026", "week": 1.0,
                                                  "total_fantasy_points_exp": 12.5}]),
                         "player_id", "full_name", "position", "posteam")
    index = populate.PlayerIndex(db)
    populate.load_feed(db, good, [2026], index)

    def broken(_seasons):
        raise ConnectionError("offline")

    populate.load_feed(db, populate.Feed("bad", PlayerExpectedPointsWeekly, broken, "player_id",
                                         "full_name", "position", "posteam"), [2026], index)
    row = db.query(PlayerExpectedPointsWeekly).one()
    assert (row.season, row.week, row.total_fantasy_points_exp) == (2026, 1, 12.5)


def test_seasons_to_load(db, monkeypatch):
    assert populate.seasons_to_load(db, 2023, reload_all=False) == [2021, 2022, 2023]
    db.add(Player(id=1, full_name="A", gsis_id="g"))
    db.add_all([PlayerStatsWeekly(player_id=1, season=s, week=1) for s in (2021, 2022, 2023)])
    db.commit()
    assert populate.seasons_to_load(db, 2023, reload_all=False) == [2023]
    assert populate.seasons_to_load(db, 2023, reload_all=True) == [2021, 2022, 2023]


def test_sync_crosswalk_fills_platform_ids(db, monkeypatch):
    db.add(Player(full_name="A", gsis_id="00-1"))
    db.commit()
    crosswalk = pl.DataFrame([
        {"gsis_id": "00-1", "yahoo_id": "29279", "sleeper_id": 3198, "pfr_id": "HenrDe00",
         "fantasypros_id": 15514, "db_season": 2026},
        {"gsis_id": "00-1", "yahoo_id": "old", "sleeper_id": None, "pfr_id": None, "fantasypros_id": None,
         "db_season": 2020},
        {"gsis_id": None, "yahoo_id": "x", "sleeper_id": None, "pfr_id": None, "fantasypros_id": None,
         "db_season": 2026},
    ])
    monkeypatch.setattr(populate.nfl, "load_ff_playerids", lambda: crosswalk)
    index = populate.PlayerIndex(db)
    populate.sync_crosswalk(db, index)
    player = db.query(Player).one()
    assert (player.yahoo_player_id, player.sleeper_player_id, player.pfr_player_id, player.fantasypros_id) == (
        "29279", "3198", "HenrDe00", "15514")
    assert index.by_pfr["HenrDe00"] == player.id and index.by_fantasypros["15514"] == player.id


def test_parse_args():
    assert populate.parse_args(["--seasons", "2025", "2026"]).seasons == [2025, 2026]
    assert populate.parse_args(["--all"]).all is True
    with pytest.raises(SystemExit):
        populate.parse_args(["--all", "--seasons", "2025"])


def test_create_tables_builds_and_stamps_fresh_database(tmp_path, monkeypatch, capsys):
    from sqlalchemy import inspect

    import database.create_tables as create_tables
    from database.db import make_engine

    fresh = make_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    monkeypatch.setattr(create_tables, "engine", fresh)
    create_tables.main()
    assert inspect(fresh).has_table("player_snap_counts_weekly")
    assert inspect(fresh).has_table("alembic_version")
    create_tables.main()                                    # re-running is safe
    assert capsys.readouterr().out.splitlines() == ["Tables created.", "Database is on the latest schema."]


def _legacy_database(path):
    """A database as main's old scripts left it: create_all() of the old models, never stamped."""
    from sqlalchemy import text

    from database.db import make_engine
    from database.models import Base

    legacy = make_engine(f"sqlite:///{path}")
    Base.metadata.create_all(legacy)
    with legacy.begin() as connection:
        for table in ("expert_rankings", "player_snap_counts_weekly", "data_loads", "external_api_calls"):
            connection.execute(text(f"DROP TABLE {table}"))
        connection.execute(text("DROP INDEX ix_players_gsis_id"))
        connection.execute(text("ALTER TABLE players DROP COLUMN fantasypros_id"))
        connection.execute(text("ALTER TABLE teams DROP COLUMN name"))
    return legacy


def test_untracked_legacy_database_is_upgraded_not_stamped(tmp_path):
    from sqlalchemy import inspect

    from database.create_tables import ensure_schema

    legacy = _legacy_database(tmp_path / "legacy.db")
    assert ensure_schema(legacy) == "upgraded-legacy"
    inspector = inspect(legacy)
    assert "fantasypros_id" in {c["name"] for c in inspector.get_columns("players")}
    assert "name" in {c["name"] for c in inspector.get_columns("teams")}
    assert inspector.has_table("expert_rankings") and inspector.has_table("data_loads")
    assert ensure_schema(legacy) == "upgraded"              # now tracked; no-op upgrade


def test_migrations_downgrade_and_upgrade_cleanly(tmp_path):
    from alembic import command
    from sqlalchemy import inspect

    from database.create_tables import alembic_config, ensure_schema

    legacy = _legacy_database(tmp_path / "roundtrip.db")
    ensure_schema(legacy)
    config = alembic_config(legacy)
    command.downgrade(config, "d7b5c4e6f566")
    assert not inspect(legacy).has_table("expert_rankings")
    command.upgrade(config, "head")
    assert inspect(legacy).has_table("expert_rankings")


def test_record_load_stamps_each_run(db):
    from database.models import DataLoad

    populate.record_load(db, [2025, 2026])
    populate.record_load(db, [2026])
    assert [r.seasons for r in db.query(DataLoad).order_by(DataLoad.id)] == ["2025,2026", "2026"]


def test_foreign_keys_are_enforced(tmp_path):
    from sqlalchemy import text

    from database.db import make_engine
    from database.models import Base

    engine = make_engine(f"sqlite:///{tmp_path / 'fk.db'}")
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1
