"""The assistant recovers when the provider rejects a tool call."""
from types import SimpleNamespace

from services import agent
from tools.definitions import TOOLS


class FlakyClient:
    """First call: provider rejects the tool call. Second: a normal answer."""

    def __init__(self):
        self.calls = []

    def complete(self, messages, tools):
        self.calls.append([m["content"] for m in messages if m["role"] == "system"])
        if len(self.calls) == 1:
            raise agent.ToolCallRejected("parameters for tool find_trades did not match schema: maxItems: got 12, want 10")
        return {"content": "Here are your trade targets.", "tool_calls": []}


def test_rejected_tool_call_is_fed_back_and_retried(session):
    client = FlakyClient()
    reply = agent.run_agent(session, client, [{"role": "user", "content": "find trades"}])
    assert reply.reply == "Here are your trade targets."
    assert any("was rejected before it ran" in text for text in client.calls[1])


def test_groq_tool_use_failed_is_recognized():
    error = SimpleNamespace(body={"error": {"code": "tool_use_failed", "message": "maxItems: got 9, want 6"}})
    assert agent._rejected_tool_call(error) == "maxItems: got 9, want 6"
    assert agent._rejected_tool_call(SimpleNamespace(body={"error": {"code": "rate_limit"}})) is None
    assert agent._rejected_tool_call(ValueError("x")) is None


def test_find_trades_accepts_ten_players():
    schema = next(t for t in TOOLS.openai_schemas() if t["function"]["name"] == "find_trades")
    assert schema["function"]["parameters"]["properties"]["my_players"]["maxItems"] == 10


def test_rate_limits_are_recognized():
    assert agent._rate_limited(SimpleNamespace(status_code=413, body=None))
    assert agent._rate_limited(SimpleNamespace(body={"error": {"code": "rate_limit_exceeded"}}))
    assert not agent._rate_limited(SimpleNamespace(status_code=400, body={"error": {"code": "tool_use_failed"}}))


def test_find_trades_tool_is_compact_and_skips_unknown_names(session, seeded):
    result = TOOLS.call(session, "find_trades", {"my_players": ["WR Player 01", "Nobody At All"], "want_position": "RB"})
    assert result["skipped_players"] == ["Nobody At All"]
    assert result["results"] and set(result["results"][0]) == {
        "give", "get", "get_ros_ppg", "verdict", "your_gain_pct", "get_playoff_schedule"}
