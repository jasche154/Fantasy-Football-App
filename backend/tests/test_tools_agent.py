import json

import pytest

from config import get_settings
from services.agent import AgentReply, run_agent
from tests.agent_fakes import ScriptedLLM, call
from tools.definitions import TOOLS, sanitize_for_agent
from tools.registry import Tool, ToolError, ToolRegistry, validate_arguments

SCHEMA = {"type": "object", "required": ["name"], "properties": {
    "name": {"type": "string"},
    "count": {"type": "integer", "minimum": 1, "maximum": 5},
    "ratio": {"type": "number"},
    "flag": {"type": "boolean"},
    "kind": {"type": "string", "enum": ["a", "b"]},
    "items": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 2},
    "short": {"type": "string", "maxLength": 3},
}}


def test_validate_arguments_accepts_good_input_and_drops_nulls():
    full = {"name": "x", "count": 3, "ratio": 2, "flag": True, "kind": "a", "items": ["p"]}
    assert validate_arguments(SCHEMA, full) == full
    cleaned = validate_arguments(SCHEMA, {"name": "x", "count": 3, "ratio": 2.5, "kind": None})
    assert cleaned == {"name": "x", "count": 3, "ratio": 2.5}


@pytest.mark.parametrize("arguments, message", [
    ({}, "Missing required"),
    ({"name": "x", "extra": 1}, "Unknown argument"),
    ({"name": 5}, "type string"),
    ({"name": "x", "count": True}, "type integer"),
    ({"name": "x", "count": "3"}, "type integer"),
    ({"name": "x", "count": 0}, ">= 1"),
    ({"name": "x", "count": 6}, "<= 5"),
    ({"name": "x", "kind": "c"}, "one of"),
    ({"name": "  "}, "can't be empty"),
    ({"name": "x", "items": []}, "at least 1"),
    ({"name": "x", "items": ["a", "b", "c"]}, "at most 2"),
    ({"name": "x", "items": [1]}, r"items\[0\]"),
    ({"name": "x", "short": "abcd"}, "at most 3 characters"),
])
def test_validate_arguments_rejects_bad_input(arguments, message):
    with pytest.raises(ToolError, match=message):
        validate_arguments(SCHEMA, arguments)


def test_registry_lookup_schemas_and_duplicates():
    registry = ToolRegistry([Tool("echo", "Echo", SCHEMA, lambda session, **kw: kw)])
    assert registry.call(None, "echo", {"name": "hi"}) == {"name": "hi"}
    assert registry.openai_schemas()[0]["type"] == "function"
    with pytest.raises(ToolError, match="Unknown tool"):
        registry.get("nope")
    with pytest.raises(ValueError, match="unique"):
        ToolRegistry([Tool("a", "", SCHEMA, print), Tool("a", "", SCHEMA, print)])


def test_every_tool_has_a_valid_object_schema():
    for tool in TOOLS:
        assert tool.parameters["type"] == "object"
        assert set(tool.parameters.get("required", [])) <= set(tool.parameters["properties"])
        assert len(tool.description) > 20


@pytest.mark.parametrize("name, arguments, key", [
    ("search_players", {"query": "chase"}, "results"),
    ("get_player_profile", {"player": "Buy Low Guy"}, "summary"),
    ("get_player_game_log", {"player": "Buy Low Guy", "last_n": 2}, "games"),
    ("compare_players", {"players": ["Buy Low Guy", "Sell High Guy"], "scoring": "half"}, "players"),
    ("get_fantasy_rankings", {"position": "WR", "limit": 3}, "results"),
    ("list_leaderboard_metrics", {}, "categories"),
    ("get_leaderboard", {"category": "rushing", "metric": "ryoe_per_att", "min_volume": 0}, "results"),
    ("evaluate_trade", {"give": ["Sell High Guy"], "get": ["Buy Low Guy", "Jamarr Chase"]}, "verdict"),
    ("get_buy_low_candidates", {"limit": 2}, "results"),
    ("get_sell_high_candidates", {"position": "RB"}, "results"),
    ("get_expert_rankings", {}, "results"),
])
def test_tools_return_data_from_the_database(session, name, arguments, key):
    result = TOOLS.call(session, name, arguments)
    assert key in result
    json.dumps(result, default=str)


def test_trade_tool_states_the_users_perspective(session):
    result = TOOLS.call(session, "evaluate_trade", {"give": ["Buy Low Guy"], "get": ["Sell High Guy"]})
    assert "USER comes out ahead" in result["perspective"]
    assert result["summary"].startswith(("You win", "You lose", "Fair trade"))


def test_system_prompt_explains_trade_verdicts(session):
    llm = ScriptedLLM([{"content": "ok", "tool_calls": []}])
    run_agent(session, llm, [{"role": "user", "content": "q"}])
    system = llm.calls[0]["messages"][0]["content"]
    assert '"big_win" mean the USER wins' in system and "expected_ppg = usage-based" in system


def test_game_log_and_profile_trim(session):
    assert len(TOOLS.call(session, "get_player_game_log", {"player": "Buy Low Guy", "last_n": 2})["games"]) == 2
    assert len(TOOLS.call(session, "get_player_profile", {"player": "Buy Low Guy"})["game_log"]) == 5


def test_trade_tool_rejects_ambiguous_names_and_players_without_current_value(session):
    from database.models import Player

    with pytest.raises(LookupError, match="several players"):
        TOOLS.call(session, "evaluate_trade", {"give": ["Mike Williams"], "get": ["Buy Low Guy"]})
    session.add(Player(full_name="Retired Receiver", position="WR", gsis_id="00-retired"))
    session.commit()
    with pytest.raises(ToolError, match="no 2026 regular-season games"):
        TOOLS.call(session, "evaluate_trade", {"give": ["Retired Receiver"], "get": ["Buy Low Guy"]})


def test_sanitize_drops_pfr_snap_data_unless_opted_in(monkeypatch):
    payload = {"snap_pct": 80, "nested": [{"snap_pct": 70, "week": 1}], "reasons": ["Snap share up 9", "TDs"]}
    assert sanitize_for_agent(payload) == {"nested": [{"week": 1}], "reasons": ["TDs"]}
    monkeypatch.setenv("AGENT_SHARE_PFR_DATA", "true")
    get_settings.cache_clear()
    assert sanitize_for_agent(payload) == payload


def test_agent_answers_directly_without_tools(session):
    llm = ScriptedLLM([{"content": "Hello!", "tool_calls": []}])
    reply = run_agent(session, llm, [{"role": "user", "content": "hi"}])
    assert reply == AgentReply("Hello!", ())
    system = llm.calls[0]["messages"][0]
    assert system["role"] == "system" and "through week 6" in system["content"]


def test_agent_runs_tools_and_feeds_results_back(session, seeded):
    llm = ScriptedLLM([call("get_player_profile", {"player": "Buy Low Guy"}),
                       {"content": "Buy him.", "tool_calls": []}])
    reply = run_agent(session, llm, [{"role": "user", "content": "Should I buy Buy Low Guy?"}])
    assert reply.reply == "Buy him."
    assert reply.tool_calls[0].name == "get_player_profile" and reply.tool_calls[0].ok
    tool_message = llm.calls[1]["messages"][-1]
    assert tool_message["role"] == "tool" and tool_message["tool_call_id"] == "c1"
    assert "snap_pct" not in tool_message["content"]          # PFR data withheld by default
    assert json.loads(tool_message["content"])["summary"]["ppg"] == 12.0


@pytest.mark.parametrize("bad_call, error", [
    (call("get_player_profile", "{not json"), "not valid JSON"),
    (call("get_player_profile", "[1, 2]"), "JSON object"),
    (call("get_player_profile", {"player": "Mike Williams"}), "matches several players"),
    (call("nope", {}), "Unknown tool"),
    (call("get_leaderboard", {"category": "rushing", "metric": "nope"}), "Unknown metric"),
])
def test_agent_reports_tool_errors_to_the_model(session, bad_call, error):
    llm = ScriptedLLM([bad_call, {"content": "Sorry.", "tool_calls": []}])
    reply = run_agent(session, llm, [{"role": "user", "content": "q"}])
    assert reply.tool_calls[0].ok is False
    assert error in llm.calls[1]["messages"][-1]["content"]


def test_agent_stops_after_max_rounds_and_asks_for_a_final_answer(session):
    llm = ScriptedLLM([call("list_leaderboard_metrics", {}), call("list_leaderboard_metrics", {}),
                       {"content": "Final.", "tool_calls": []}])
    reply = run_agent(session, llm, [{"role": "user", "content": "q"}], max_rounds=2)
    assert reply.reply == "Final." and len(reply.tool_calls) == 2
    assert llm.calls[-1]["tools"] is None


def test_agent_survives_unexpected_tool_failures(session):
    def explode(_session, **_kwargs):
        raise RuntimeError("upstream down: secret detail")

    registry = ToolRegistry([Tool("flaky", "Always fails.", {"type": "object", "properties": {}}, explode)])
    llm = ScriptedLLM([call("flaky", {}), {"content": "Data unavailable.", "tool_calls": []}])
    reply = run_agent(session, llm, [{"role": "user", "content": "q"}], registry=registry)
    assert reply.reply == "Data unavailable." and reply.tool_calls[0].ok is False
    tool_message = llm.calls[1]["messages"][-1]["content"]
    assert "failed unexpectedly" in tool_message and "secret detail" not in tool_message


def test_tool_player_names_have_a_length_limit(session):
    with pytest.raises(ToolError, match="at most 80"):
        TOOLS.call(session, "get_player_profile", {"player": "x" * 81})


def test_trending_tool_reports_sleeper_outage_to_the_model(session, monkeypatch):
    import integrations.sleeper as sleeper

    def down(*_args, **_kwargs):
        raise sleeper.SleeperError("timeout")

    monkeypatch.setattr(sleeper, "fetch_trending", down)
    llm = ScriptedLLM([call("get_trending_players", {}), {"content": "Sleeper is down.", "tool_calls": []}])
    reply = run_agent(session, llm, [{"role": "user", "content": "who is trending?"}])
    assert reply.tool_calls[0].ok is False
    assert "Sleeper trending data is unavailable" in llm.calls[1]["messages"][-1]["content"]


def test_programming_errors_are_not_passed_through_as_tool_messages(session):
    def buggy(_session, **_kwargs):
        return {}["missing"]          # KeyError: a bug, not a user-facing condition

    registry = ToolRegistry([Tool("buggy", "Has a bug.", {"type": "object", "properties": {}}, buggy)])
    llm = ScriptedLLM([call("buggy", {}), {"content": "ok", "tool_calls": []}])
    run_agent(session, llm, [{"role": "user", "content": "q"}], registry=registry)
    content = llm.calls[1]["messages"][-1]["content"]
    assert "failed unexpectedly" in content and "missing" not in content


@pytest.mark.parametrize("name, arguments", [
    ("get_player_profile", {"player": "Buy Low Guy", "season": 2030}),
    ("get_player_game_log", {"player": "Buy Low Guy", "season": 2030}),
    ("compare_players", {"players": ["Buy Low Guy", "Sell High Guy"], "season": 2030}),
    ("get_leaderboard", {"category": "rushing", "metric": "ryoe_per_att", "season": 2030}),
])
def test_tools_reject_seasons_without_data(session, name, arguments):
    with pytest.raises(ToolError, match="No data loaded for season 2030"):
        TOOLS.call(session, name, arguments)

