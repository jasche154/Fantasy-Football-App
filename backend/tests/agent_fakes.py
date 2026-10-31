"""Shared scripted LLM for agent tests: replays canned steps and records every request.

A step is a response dict ({"content", "tool_calls"}) or an exception to raise (ToolCallRejected,
AgentUnavailable, ...). Nothing touches the network.
"""
import json
from typing import Any


class ScriptedLLM:
    """Replays steps in order. `profile` / `provider` mimic a real client for budget and fallback tests."""

    def __init__(self, steps, profile: Any = None, provider: str | None = None):
        self.steps = list(steps)
        self.requests: list[dict] = []
        if profile is not None:
            self.profile = profile
        if provider is not None:
            self.last_provider = provider

    def complete(self, messages, tools):
        self.requests.append({"messages": [dict(m) for m in messages], "tools": tools})
        if not self.steps:
            raise AssertionError("ScriptedLLM ran out of steps")
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    @property
    def calls(self) -> list[dict]:
        """Alias of `requests` (older tests read `.calls`)."""
        return self.requests

    @property
    def messages(self) -> list[dict]:
        """Messages of the latest request."""
        return self.requests[-1]["messages"] if self.requests else []

    @property
    def seen_tools(self) -> list[list[str]]:
        """Tool names offered on each request."""
        return [[t["function"]["name"] for t in r["tools"] or []] for r in self.requests]

    def system_texts(self, n: int) -> list[str]:
        """System and user message texts of request n (app notes go in either role)."""
        return [m["content"] for m in self.requests[n]["messages"] if m["role"] in ("system", "user")]


def call(name: str, arguments: Any, call_id: str = "c1", **extra) -> dict:
    """One tool call step. `arguments` may be a dict or a raw string (to test bad JSON)."""
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return {"content": None, "tool_calls": [{"id": call_id, "name": name, "arguments": raw, **extra}]}


def calls(*items: tuple[str, Any]) -> dict:
    """Several tool calls in one round: calls(("a", {...}), ("b", {...}))."""
    return {"content": None, "tool_calls": [
        {"id": f"c{i}", "name": name, "arguments": json.dumps(args)} for i, (name, args) in enumerate(items, 1)]}


def answer(text: str) -> dict:
    return {"content": text, "tool_calls": []}
