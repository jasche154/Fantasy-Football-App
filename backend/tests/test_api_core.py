from tests.conftest import CURRENT


def test_health_and_meta(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    meta = client.get("/api/meta").json()
    assert meta["current_season"] == CURRENT and meta["latest_week"] == 6
    assert meta["current_week"] == 7                      # no schedule seeded: the week after the latest stats
    assert meta["seasons"] == [2025, 2026] and meta["positions"] == ["QB", "RB", "WR", "TE"]
    assert meta["scoring_presets"] == ["ppr", "half", "std"]


def test_player_search_and_validation(client, seeded):
    results = client.get("/api/players/search", params={"q": "chase"}).json()["results"]
    assert results[0] == {"id": seeded["chase"], "name": "Ja'Marr Chase", "position": "WR", "team": "CIN"}
    assert client.get("/api/players/search", params={"q": ""}).status_code == 422
    assert client.get("/api/players/search", params={"q": "x", "position": "K"}).status_code == 422


def test_player_detail(client, seeded):
    body = client.get(f"/api/players/{seeded['buy']}", params={"scoring": "half"}).json()
    assert body["scoring"] == "half" and body["summary"]["ppg"] == 9.5
    assert client.get("/api/players/999999").status_code == 404
    assert client.get(f"/api/players/{seeded['buy']}", params={"season": 2001}).status_code == 404


def test_rankings_filters_and_ecr(client, seeded):
    body = client.get("/api/rankings", params={"position": "WR", "limit": 5}).json()
    assert body["season"] == CURRENT and body["weeks_remaining"] == 11 and len(body["results"]) == 5
    assert all(r["player"]["position"] == "WR" for r in body["results"])
    assert [r["rank"] for r in body["results"]] == [1, 2, 3, 4, 5]
    everyone = client.get("/api/rankings", params={"limit": 500}).json()["results"]
    chase = next(r for r in everyone if r["player"]["id"] == seeded["chase"])
    assert chase["ecr"] == 3.4
    assert client.get("/api/rankings", params={"teams": 2}).status_code == 422
    assert client.get("/api/rankings", params={"scoring": "bogus"}).status_code == 422


def test_leaderboards(client, seeded):
    metrics = client.get("/api/leaderboards/metrics").json()["categories"]
    assert [c["key"] for c in metrics] == ["rushing", "receiving", "passing", "usage"]
    body = client.get("/api/leaderboards", params={"category": "rushing", "metric": "ryoe_per_att",
                                                   "min_volume": 0}).json()
    assert body["label"] == "Rush yards over expected / att" and body["min_volume"] == 0
    assert body["results"][0]["player"]["id"] == seeded["rb1"]
    default = client.get("/api/leaderboards", params={"category": "rushing", "metric": "ryoe_per_att"}).json()
    assert default["min_volume"] == 50
    assert client.get("/api/leaderboards", params={"category": "rushing", "metric": "x"}).status_code == 422


def test_free_ecr_is_positional_and_grouped_by_position(client, session):
    from database.models import ExpertRanking

    session.add_all([ExpertRanking(player_name="QB One", position="QB", source="nflverse_ffrankings",
                                   ranking_type="weekly", rank=1, ecr=1.2, pos_rank="QB1"),
                     ExpertRanking(player_name="RB One", position="RB", source="nflverse_ffrankings",
                                   ranking_type="weekly", rank=1, ecr=1.1, pos_rank="RB1")])
    session.commit()
    body = client.get("/api/experts/rankings").json()
    assert body["scope"] == "positional"
    assert [r["position"] for r in body["results"]] == ["QB", "RB", "WR"]


def test_expert_rankings_from_free_snapshot(client):
    body = client.get("/api/experts/rankings", params={"position": "WR"}).json()
    assert body["source"] == "nflverse_ffrankings" and body["warning"] is None
    assert body["results"][0]["pos_rank"] == "WR3"


def test_untrusted_host_header_is_rejected(client):
    # Blocks DNS-rebinding pages that point their own hostname at 127.0.0.1.
    assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 400


def test_cross_site_writes_are_blocked_but_tools_without_origin_work(client):
    blocked = client.post("/api/health", headers={"origin": "https://evil.example"})
    assert blocked.status_code == 403 and blocked.json() == {"detail": "Cross-site request blocked."}
    assert client.post("/api/health", headers={"origin": "http://localhost:5173"}).status_code == 405
    assert client.post("/api/health").status_code == 405       # no Origin (curl, scripts): routed normally
    assert client.get("/api/health", headers={"origin": "https://evil.example"}).status_code == 200


def test_search_treats_like_wildcards_literally(client):
    assert client.get("/api/players/search", params={"q": "%"}).json()["results"] == []


def test_legacy_rushing_endpoint(client):
    body = client.get("/api/rankings/rushing", params={"season": CURRENT, "min_attempts": 0}).json()
    assert body["results"][0] == {"player": "RB Player 01", "rush_yards_over_expected": 0.95}
    pct = client.get("/api/rankings/rushing", params={"season": CURRENT, "min_attempts": 0,
                                                       "metric": "rush_pct_over_expected"}).json()
    assert pct["results"][0]["rush_pct_over_expected"] == 0.5     # legacy 0-1 scale
    assert client.get("/api/rankings/rushing", params={"metric": "bogus"}).status_code == 422
    assert client.get("/api/rankings/rushing", params={"limit": -1}).status_code == 422
