import asyncio
import json

import pytest
from sqlalchemy.orm import sessionmaker

pytest.importorskip("mcp")

import mcp_server  # noqa: E402  (needs the optional mcp package)
from mcp import Client  # noqa: E402


@pytest.fixture
def server(engine, seeded, monkeypatch):
    monkeypatch.setattr(mcp_server, "SessionLocal", sessionmaker(bind=engine))
    return mcp_server.build_server()


def test_mcp_lists_every_registry_tool(server):
    async def scenario():
        async with Client(server) as client:
            return await client.list_tools()

    tools = asyncio.run(scenario()).tools
    names = {t.name for t in tools}
    assert {"evaluate_trade", "get_buy_low_candidates", "search_players"} <= names
    trade = next(t for t in tools if t.name == "evaluate_trade")
    assert trade.input_schema["required"] == ["give", "get"]


def test_mcp_calls_tools_and_reports_errors(server):
    async def scenario():
        async with Client(server) as client:
            ok = await client.call_tool("search_players", {"query": "buy low"})
            bad = await client.call_tool("get_player_profile", {"player": "Mike Williams"})
            return ok, bad

    ok, bad = asyncio.run(scenario())
    assert not ok.is_error and json.loads(ok.content[0].text)["results"][0]["name"] == "Buy Low Guy"
    assert bad.is_error and "several players" in json.loads(bad.content[0].text)["error"]


def test_run_tool_withholds_snap_data(engine, seeded, monkeypatch):
    monkeypatch.setattr(mcp_server, "SessionLocal", sessionmaker(bind=engine))
    text, is_error = mcp_server.run_tool("get_player_profile", {"player": "Buy Low Guy"})
    assert not is_error and "snap_pct" not in text


def test_run_tool_hides_unexpected_errors(engine, seeded, monkeypatch):
    from tools.registry import Tool, ToolRegistry

    def explode(_session, **_kwargs):
        raise RuntimeError("db password=hunter2")

    monkeypatch.setattr(mcp_server, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(mcp_server, "TOOLS", ToolRegistry([Tool("flaky", "x", {"type": "object", "properties": {}}, explode)]))
    text, is_error = mcp_server.run_tool("flaky", {})
    assert is_error and "hunter2" not in text and "failed unexpectedly" in text

