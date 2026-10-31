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


class InventingClient:
    """First answer names a player no tool returned; the revision drops him."""

    def __init__(self):
        self.calls = []

    def complete(self, messages, tools):
        self.calls.append([m["content"] for m in messages if m["role"] == "system"])
        if len(self.calls) == 1:
            return {"content": "Pick up Mike Williams and Ja'Marr Chase.", "tool_calls": []}
        return {"content": "Pick up Ja'Marr Chase.", "tool_calls": []}


def test_answers_naming_players_no_tool_returned_get_one_revision(session):
    client = InventingClient()
    reply = agent.run_agent(session, client, [{"role": "user", "content": "Is Ja'Marr Chase a good add?"}])
    assert reply.reply == "Pick up Ja'Marr Chase."
    assert any("Mike Williams" in text and "no tool returned" in text for text in client.calls[1])
    assert len(client.calls) == 2


def test_ungrounded_players_checks_user_and_tool_messages_only():
    messages = [{"role": "user", "content": "trade Josh Allen?"}, {"role": "tool", "content": '{"get": "Joe Burrow"}'},
                {"role": "assistant", "content": "Tee Higgins"}]
    names = {"Josh Allen", "Joe Burrow", "Tee Higgins", "Jalen Reagor"}
    answer = "Josh Allen for Joe Burrow, then add Tee Higgins or Jalen Reagor."
    assert agent.ungrounded_players(answer, messages, names) == ["Jalen Reagor", "Tee Higgins"]


def test_replies_are_plain_text_and_schedules_read_in_words():
    from tools.definitions import schedule_words

    assert agent.plain_text("## Trades\n- **Give** Bryce Young") == "Trades\n- Give Bryce Young"
    assert schedule_words(0.94) == "6% harder than average (fantasy playoff weeks)"
    assert schedule_words(1.052) == "5% easier than average (fantasy playoff weeks)"
    assert schedule_words(1.001) == "average (fantasy playoff weeks)"
