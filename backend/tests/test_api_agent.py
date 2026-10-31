import pytest

from config import get_settings
from services import agent as agent_module
from tests.agent_fakes import ScriptedLLM


def test_meta_reports_llm_availability(client, monkeypatch):
    assert client.get("/api/meta").json()["integrations"]["llm"] is False
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    get_settings.cache_clear()
    assert client.get("/api/integrations/status").json()["llm"] is True


def test_tool_endpoints(client):
    names = [t["name"] for t in client.get("/api/agent/tools").json()["tools"]]
    assert "evaluate_trade" in names and "get_buy_low_candidates" in names
    ok = client.post("/api/agent/tools/search_players", json={"arguments": {"query": "buy"}})
    assert ok.status_code == 200 and ok.json()["result"]["results"][0]["name"] == "Buy Low Guy"
    assert client.post("/api/agent/tools/nope", json={"arguments": {}}).status_code == 404
    bad = client.post("/api/agent/tools/search_players", json={"arguments": {"query": 5}})
    assert bad.status_code == 422
    ambiguous = client.post("/api/agent/tools/get_player_profile", json={"arguments": {"player": "Mike Williams"}})
    assert ambiguous.status_code == 404
    bad_metric = client.post("/api/agent/tools/get_leaderboard",
                             json={"arguments": {"category": "rushing", "metric": "zzz"}})
    assert bad_metric.status_code == 422


def test_tool_endpoint_withholds_snap_data(client):
    result = client.post("/api/agent/tools/get_player_profile",
                         json={"arguments": {"player": "Buy Low Guy"}}).json()["result"]
    assert "snap_pct" not in result["summary"]
    assert all("snap_pct" not in game for game in result["game_log"])


def test_chat_requires_llm_key(client):
    response = client.post("/api/agent/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == 503 and "GROQ_API_KEY" in response.json()["detail"]


def use_llm(monkeypatch, *steps):
    monkeypatch.setattr("api.agent_routes.build_llm_client", lambda _settings: ScriptedLLM(steps))


def test_chat_with_fake_llm(client, monkeypatch):
    use_llm(monkeypatch, {"content": None, "tool_calls": [
        {"id": "1", "name": "get_buy_low_candidates", "arguments": "{\"limit\": 1}"}]},
        {"content": "Buy Low Guy.", "tool_calls": []})
    body = client.post("/api/agent/chat", json={"messages": [{"role": "user", "content": "buy lows?"}]}).json()
    assert body["reply"] == "Buy Low Guy." and body["memory"] == []
    assert body["tool_calls"] == [{"name": "get_buy_low_candidates", "arguments": {"limit": 1}, "ok": True}]
    assert body["stop_reason"] == "answer" and body["unverified"] == [] and body["provider"] is None
    assert set(body["data_as_of"]) == {"season", "week"} and isinstance(body["suggestions"], list)
    assert "evidence" not in body


@pytest.mark.parametrize("error, status", [
    (agent_module.AgentUnavailable("down"), 502),
    (agent_module.AgentUnavailable("slow down", kind="rate_limit", retry_after=42), 429),
    (agent_module.AgentUnavailable("too big", kind="too_large"), 413),
])
def test_chat_provider_failures_map_to_status_codes(client, monkeypatch, error, status):
    use_llm(monkeypatch, error)
    response = client.post("/api/agent/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == status
    if status == 429:
        assert response.headers["Retry-After"] == "42"
    if status == 413:
        assert "narrower" in response.json()["detail"]


def test_chat_accepts_long_assistant_replies_but_not_long_questions(client, monkeypatch):
    use_llm(monkeypatch, {"content": "ok", "tool_calls": []})
    long_reply = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "x" * 9000},
                  {"role": "user", "content": "more?"}]
    assert client.post("/api/agent/chat", json={"messages": long_reply}).status_code == 200
    long_question = [{"role": "user", "content": "x" * 4001}]
    assert client.post("/api/agent/chat", json={"messages": long_question}).status_code == 422


def test_chat_rejects_an_unknown_league(client, monkeypatch):
    use_llm(monkeypatch, {"content": "ok", "tool_calls": []})
    response = client.post("/api/agent/chat", json={"messages": [{"role": "user", "content": "q"}], "league_id": 999})
    assert response.status_code == 422


@pytest.mark.parametrize("messages", [[], [{"role": "system", "content": "x"}], [{"role": "user", "content": ""}]])
def test_chat_validates_messages(client, messages):
    assert client.post("/api/agent/chat", json={"messages": messages}).status_code == 422


def test_tool_endpoint_status_codes_for_outages_and_bad_seasons(client, monkeypatch):
    import integrations.sleeper as sleeper

    def down(*_args, **_kwargs):
        raise sleeper.SleeperError("timeout")

    monkeypatch.setattr(sleeper, "fetch_trending", down)
    assert client.post("/api/agent/tools/get_trending_players", json={"arguments": {}}).status_code == 502
    bad_season = client.post("/api/agent/tools/get_player_profile",
                             json={"arguments": {"player": "Buy Low Guy", "season": 2030}})
    assert bad_season.status_code == 422 and "2030" in bad_season.json()["detail"]

