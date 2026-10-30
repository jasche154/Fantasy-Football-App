import pytest

from config import get_settings
from services import agent as agent_module


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


def test_chat_with_fake_llm(client, monkeypatch):
    class FakeGroq:
        def __init__(self, api_key, model):
            self.responses = [{"content": None, "tool_calls": [
                {"id": "1", "name": "get_buy_low_candidates", "arguments": "{\"limit\": 1}"}]},
                {"content": "Buy Low Guy.", "tool_calls": []}]

        def complete(self, messages, tools):
            return self.responses.pop(0)

    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    get_settings.cache_clear()
    monkeypatch.setattr("api.agent_routes.GroqClient", FakeGroq)
    body = client.post("/api/agent/chat", json={"messages": [{"role": "user", "content": "buy lows?"}]}).json()
    assert body == {"reply": "Buy Low Guy.", "tool_calls": [
        {"name": "get_buy_low_candidates", "arguments": {"limit": 1}, "ok": True}], "memory": []}


def test_chat_upstream_failure_is_502(client, monkeypatch):
    class BrokenGroq:
        def __init__(self, *_):
            pass

        def complete(self, messages, tools):
            raise agent_module.AgentUnavailable("down")

    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    get_settings.cache_clear()
    monkeypatch.setattr("api.agent_routes.GroqClient", BrokenGroq)
    response = client.post("/api/agent/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == 502


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

