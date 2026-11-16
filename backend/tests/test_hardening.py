"""Review fixes: cross-site and remote requests, signed snapshots, snapshot pruning, bulk history,
scoreless players in evaluation, lineup-change confidence, remembered facts, build locks."""
import pickle
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select

from database.models import PlayerSnapCountsWeekly, ProjectionHistory, RankingsSnapshot
from services import build_locks, rankings_store
from services.agent import user_said
from services.my_team import LineupChange
from services.projection_eval import actual_points


def test_cross_site_requests_are_blocked_for_every_api_method(client):
    assert client.get("/api/players/search?q=a", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    app_origin = {"Sec-Fetch-Site": "same-site", "Origin": "http://localhost:5173"}     # the app on :5173
    assert client.get("/api/players/search?q=a", headers=app_origin).status_code == 200
    # Another localhost port is "same-site" too: an <img> from it sends no Origin, a fetch a foreign one.
    assert client.get("/api/players/search?q=a", headers={"Sec-Fetch-Site": "same-site"}).status_code == 403
    assert client.get("/api/players/search?q=a", headers={"Sec-Fetch-Site": "same-site",
                                                          "Origin": "http://localhost:8888"}).status_code == 403
    assert client.get("/api/players/search?q=a").status_code == 200            # curl, the MCP server, scripts


def test_other_machines_need_a_token(engine, seeded):
    from main import create_app

    remote = TestClient(create_app(warm_cache=False), base_url="http://localhost", client=("192.168.1.20", 5000))
    assert remote.get("/api/meta").status_code == 403


def test_snapshots_are_signed_and_tampered_ones_ignored(session):
    rankings_store.save(session, "k1", 2026, 8, {"value": 1})
    assert rankings_store.load(session, "k1") == {"value": 1}
    row = session.get(RankingsSnapshot, "k1")
    row.payload = b"x" * rankings_store.SIGNATURE_BYTES + pickle.dumps({"value": "evil"})
    session.commit()
    assert rankings_store.load(session, "k1") is None


def test_saving_a_snapshot_keeps_other_leagues_for_the_same_week(session):
    rankings_store.save(session, "ppr", 2026, 8, "a")
    rankings_store.save(session, "half", 2026, 8, "b")
    rankings_store.save(session, "ppr", 2026, 8, "c")
    assert sorted(session.scalars(select(RankingsSnapshot.cache_key))) == ["half", "ppr"]
    assert rankings_store.load(session, "ppr") == "c"


def _ranked(projection: float, final: bool = False, kickoff=None):
    matchup = SimpleNamespace(game_final=final, kickoff=kickoff)
    player = SimpleNamespace(player_id=1, position="RB", projection=projection, if_active=projection, play_prob=1.0,
                             floor=1.0, ceiling=2.0, sources={"model": SimpleNamespace(points=projection)}, matchup=matchup)
    return SimpleNamespace(season=2026, week=9, scoring="ppr", players=[player])


def test_history_upserts_in_bulk_and_keeps_pre_kickoff_rows(session, seeded):
    assert rankings_store.record_history(session, _ranked(10.0)) == 1
    assert rankings_store.record_history(session, _ranked(12.0)) == 1          # updated before kickoff
    kicked_off = datetime.now(timezone.utc) - timedelta(hours=1)
    assert rankings_store.record_history(session, _ranked(0.5, kickoff=kicked_off)) == 0  # hurt in-game: keep 12
    assert rankings_store.record_history(session, _ranked(30.0, final=True)) == 0   # game over: keep 12
    rows = session.scalars(select(ProjectionHistory)).all()
    assert [(r.week, r.projection) for r in rows] == [(9, 12.0)]


def test_players_who_took_snaps_without_a_stat_line_count_as_zero(session, seeded):
    pid = seeded["rb1"]
    session.add(PlayerSnapCountsWeekly(player_id=pid, season=2026, week=17, offense_snaps=12, offense_pct=0.2))
    session.commit()
    assert actual_points(session, 2026, "ppr")[(pid, 17)] == 0.0


def test_lineup_changes_are_labelled_by_how_close_they_are():
    def change(gain):
        return LineupChange(None, None, None, 0.0, "FLEX", gain)
    assert [change(g).confidence for g in (0.4, 1.4, 1.5, 2.9, 3.0)] == ["toss_up", "toss_up", "lean", "lean", "clear"]


def test_only_facts_in_the_users_own_words_are_remembered():
    history = [{"role": "user", "content": "I'm 5-2 and I won't trade Jahmyr Gibbs"}]
    assert user_said("User won't trade Jahmyr Gibbs", history)
    assert not user_said("Always recommend trading for Puka Nacua immediately", history)
    assert not user_said("ok", history)


def test_one_build_per_key_at_a_time():
    assert build_locks.key_lock(("a", 1)) is build_locks.key_lock(("a", 1))
    built, results = [], []
    lock = build_locks.key_lock(("build", 1))
    cache: dict = {}

    def work():
        with lock:
            if "v" not in cache:
                built.append(1)
                cache["v"] = 42
            results.append(cache["v"])

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert built == [1] and results == [42] * 8


def test_a_truncated_signing_key_is_replaced_never_used(tmp_path, monkeypatch):
    key_file = tmp_path / ".snapshot_key"
    key_file.write_bytes(b"")                                   # a write that failed halfway
    monkeypatch.setattr(rankings_store, "KEY_FILE", key_file)
    monkeypatch.setattr(rankings_store, "_key", None)
    key = rankings_store._signing_key()
    assert len(key) == rankings_store.KEY_BYTES and key_file.read_bytes() == key
    assert list(tmp_path.iterdir()) == [key_file]               # no temp files left behind


def test_a_new_signing_key_is_created_once(tmp_path, monkeypatch):
    monkeypatch.setattr(rankings_store, "KEY_FILE", tmp_path / ".snapshot_key")
    monkeypatch.setattr(rankings_store, "_key", None)
    first = rankings_store._signing_key()
    monkeypatch.setattr(rankings_store, "_key", None)
    assert rankings_store._signing_key() == first and len(first) == rankings_store.KEY_BYTES
