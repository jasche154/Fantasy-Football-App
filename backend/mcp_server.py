"""MCP server exposing the fantasy-football tools to Claude Desktop, Claude Code, or any MCP client.

Run from backend/ (stdio transport):  python mcp_server.py
Requires the optional `mcp` package:  pip install -r requirements-mcp.txt

It serves the same tool registry as the REST API (/api/agent/tools), with the same
argument validation and the same PFR-data withholding.
"""
import asyncio
import json
import logging
from typing import Any

import mcp_types as types
from mcp.server.lowlevel.server import Server
from mcp.server.stdio import stdio_server

from database.db import SessionLocal
from tools.definitions import TOOL_ERRORS, TOOLS, sanitize_for_agent
from tools.shaping import fit

log = logging.getLogger("mcp_server")

# MCP clients (Claude Desktop/Code) have large contexts; this only stops runaway results.
MCP_RESULT_CHARS = 24_000

INSTRUCTIONS = ("Fantasy football data tools backed by nflverse stats. Use them for every number; "
                "players can be referenced by name or id. Projections and trade values come from this "
                "app's model; expected points come from nflverse's usage model. With more than one "
                "imported Yahoo league, pass `league` to league tools.")


def run_tool(name: str, arguments: dict[str, Any] | None) -> tuple[str, bool]:
    """Run one tool in its own DB session; returns (json_text, is_error)."""
    with SessionLocal() as session:
        try:
            result = sanitize_for_agent(TOOLS.call(session, name, arguments or {}))
        except TOOL_ERRORS as exc:   # bad arguments, unknown/ambiguous player, no data, source down
            return json.dumps({"error": str(exc)}), True
        except Exception:   # a bug or DB error: details stay in the server log (stderr)
            log.exception("Tool %s failed", name)
            return json.dumps({"error": "The tool failed unexpectedly."}), True
    return fit(result, MCP_RESULT_CHARS, TOOLS.get(name).trim_keys), False


async def list_tools(_ctx, _params) -> types.ListToolsResult:
    return types.ListToolsResult(tools=[
        types.Tool(name=t.name, description=t.description, input_schema=dict(t.parameters)) for t in TOOLS])


async def call_tool(_ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
    # Tools use synchronous SQLAlchemy; keep them off the event loop.
    text, is_error = await asyncio.to_thread(run_tool, params.name, params.arguments)
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=is_error)


def build_server() -> Server:
    return Server("fantasy-football-toolkit", instructions=INSTRUCTIONS,
                  on_list_tools=list_tools, on_call_tool=call_tool)


async def main() -> None:
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
