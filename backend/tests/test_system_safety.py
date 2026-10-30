"""Engineering and safety: the API token, backups, job status, unique Yahoo ids, no leaked keys."""
import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from config import get_settings
from database.models import Player
from integrations import odds_api
from integrations.weekly_sources import SourceError
from services import auto_update, backup, system_status


@pytest.fixture
def token_client(engine, seeded, monkeypatch):
    from main import create_app
    from database.db import get_session
    from sqlalchemy.orm import sessionmaker

    monkeypatch.setenv("API_TOKEN", "s3cret-token")
    get_settings.cache_clear()
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override():
        with factory() as db_session:
            yield db_session

    app = create_app(warm_cache=False)
    app.dependency_overrides[get_session] = override
    with TestClient(app, base_url="http://localhost") as test_client:
        yield test_client


def test_api_token_is_required_when_set(token_client):
    assert token_client.get("/api/players/search?q=a").status_code == 401
    assert token_client.get("/api/players/search?q=a", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert token_client.get("/api/players/search?q=a", headers={"Authorization": "s3cret-token"}).status_code == 401
    ok = token_client.get("/api/players/search?q=a", headers={"Authorization": "Bearer s3cret-token"})
    assert ok.status_code == 200
    meta = token_client.get("/api/meta")                     # open, so the app can ask for the token
    assert meta.status_code == 200 and meta.json()["auth_required"] is True


def test_token_401_carries_cors_headers_so_the_browser_can_read_it(token_client):
    response = token_client.get("/api/system/status", headers={"Origin": "http://localhost:5173"})
    assert response.status_code == 401
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"
    preflight = token_client.options("/api/system/status", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization"})
    assert preflight.status_code == 200


def test_no_token_needed_by_default(client):
    assert client.get("/api/players/search?q=a").status_code == 200
    assert client.get("/api/meta").json()["auth_required"] is False


def _sqlite_file(tmp_path):
    db = tmp_path / "fantasy.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("create table t (x int)")
        conn.execute("insert into t values (42)")
        conn.commit()
    return db


def test_backups_copy_the_database_and_keep_the_newest(tmp_path):
    db = _sqlite_file(tmp_path)
    folder = tmp_path / "backups"
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for day in range(5):
        backup.backup_database(f"sqlite:///{db}", folder, keep=3, now=start + timedelta(days=day))
    kept = backup.backups(folder)
    assert [p.name for p in kept] == ["fantasy-20261003-000000.db", "fantasy-20261004-000000.db",
                                      "fantasy-20261005-000000.db"]
    with closing(sqlite3.connect(kept[-1])) as conn:
        assert conn.execute("select x from t").fetchone() == (42,)


def test_backups_refuse_in_memory_databases_and_run_once_a_day(tmp_path):
    with pytest.raises(ValueError):
        backup.backup_database("sqlite://", tmp_path, keep=3)
    now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
    assert backup.due(None, now)
    assert not backup.due({"at": (now - timedelta(hours=3)).isoformat()}, now)
    assert backup.due({"at": (now - timedelta(hours=25)).isoformat()}, now)
    assert backup.due({"at": "garbage"}, now)


def test_status_records_survive_and_the_endpoint_reports_them(client, session):
    system_status.record(session, "odds_credits", {"remaining": 1234})
    body = client.get("/api/system/status").json()
    assert body["odds_credits"]["remaining"] == 1234
    assert {"auto_update", "backup", "data", "auth_required"} <= set(body)


def test_auto_update_check_records_failures_by_type_only(engine, seeded, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=engine)
    secret_url = "https://api.the-odds-api.com/v4/x?apiKey=SUPERSECRET"

    def boom(_factory, **_):
        raise httpx.ConnectError(f"failed {secret_url}")

    monkeypatch.setattr(auto_update, "run_once", boom)
    monkeypatch.setattr(backup, "run", lambda session: None)
    status = auto_update.check(factory)
    assert status["ok"] is False and status["error"] == "ConnectError"
    with factory() as session:
        stored = system_status.read(session, "auto_update")
    assert "SUPERSECRET" not in str(stored)


def test_odds_api_key_never_reaches_logs_or_errors(caplog):
    key = "ODDS-KEY-123456"

    def handler(request):
        raise httpx.ConnectError(f"cannot connect to {request.url}", request=request)

    client = odds_api.OddsApiClient(key, httpx.Client(transport=httpx.MockTransport(handler)))
    caplog.set_level(logging.DEBUG)
    with pytest.raises(SourceError) as error:
        client.events()
    assert key not in str(error.value)
    assert key not in caplog.text
    for status in (401, 429, 500):
        bad = odds_api.OddsApiClient(key, httpx.Client(transport=httpx.MockTransport(lambda r, s=status: httpx.Response(s))))
        with pytest.raises(SourceError) as error:
            bad.events()
        assert key not in str(error.value)


def test_two_players_cannot_share_a_yahoo_id(session):
    session.add_all([Player(full_name="A One", position="WR", yahoo_player_id="999001"),
                     Player(full_name="B Two", position="WR", yahoo_player_id="999001")])
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    session.add_all([Player(full_name="C Three", position="WR"), Player(full_name="D Four", position="WR")])
    session.commit()                                          # missing ids are fine
