"""A provider-neutral registry of agent tools.

Each tool has a JSON-schema parameter spec (OpenAI / Groq function-calling format) and a
handler `handler(session, **arguments)`. Arguments are validated against the schema
before the handler runs, so a model can't pass unknown keys, wrong types, or values
outside the allowed range.
"""
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,), "integer": (int,), "number": (int, float), "boolean": (bool,),
    "array": (list,), "object": (dict,),
}


class ToolError(ValueError):
    """Raised for unknown tools or invalid arguments; the message is safe to show a model."""


class ToolUnavailable(RuntimeError):
    """A tool's upstream data source is down; the message is safe to show a model."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: Mapping[str, Any]
    handler: Callable[..., Any]

    def definition(self) -> dict:
        return {"name": self.name, "description": self.description, "parameters": dict(self.parameters)}

    def openai_schema(self) -> dict:
        return {"type": "function", "function": self.definition()}


def _check_type(name: str, value: Any, spec: Mapping[str, Any]) -> None:
    expected = spec.get("type")
    allowed = JSON_TYPES.get(expected)
    # bool is a subclass of int in Python; don't let True pass as an integer.
    if allowed and (not isinstance(value, allowed) or (expected != "boolean" and isinstance(value, bool))):
        raise ToolError(f"'{name}' must be of type {expected}.")
    if "enum" in spec and value not in spec["enum"]:
        raise ToolError(f"'{name}' must be one of {spec['enum']}.")
    if expected in ("integer", "number"):
        if "minimum" in spec and value < spec["minimum"]:
            raise ToolError(f"'{name}' must be >= {spec['minimum']}.")
        if "maximum" in spec and value > spec["maximum"]:
            raise ToolError(f"'{name}' must be <= {spec['maximum']}.")
    if expected == "string" and not value.strip():
        raise ToolError(f"'{name}' can't be empty.")
    if expected == "string" and "maxLength" in spec and len(value) > spec["maxLength"]:
        raise ToolError(f"'{name}' must be at most {spec['maxLength']} characters.")
    if expected == "array":
        if "minItems" in spec and len(value) < spec["minItems"]:
            raise ToolError(f"'{name}' needs at least {spec['minItems']} items.")
        if "maxItems" in spec and len(value) > spec["maxItems"]:
            raise ToolError(f"'{name}' allows at most {spec['maxItems']} items.")
        for index, item in enumerate(value):
            _check_type(f"{name}[{index}]", item, spec.get("items", {}))


def validate_arguments(parameters: Mapping[str, Any], arguments: Mapping[str, Any] | None) -> dict:
    arguments = dict(arguments or {})
    properties = parameters.get("properties", {})
    unknown = set(arguments) - set(properties)
    if unknown:
        raise ToolError(f"Unknown argument(s): {sorted(unknown)}. Allowed: {sorted(properties)}.")
    missing = [key for key in parameters.get("required", []) if arguments.get(key) is None]
    if missing:
        raise ToolError(f"Missing required argument(s): {missing}.")
    cleaned = {}
    for key, value in arguments.items():
        if value is None:
            continue
        _check_type(key, value, properties[key])
        cleaned[key] = value
    return cleaned


class ToolRegistry:
    def __init__(self, tools: list[Tool]):
        names = [t.name for t in tools]
        if len(names) != len(set(names)):
            raise ValueError("Tool names must be unique.")
        self._tools = {t.name: t for t in tools}

    def __iter__(self):
        return iter(self._tools.values())

    def get(self, name: str) -> Tool:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(f"Unknown tool '{name}'. Available: {sorted(self._tools)}.")
        return tool

    def definitions(self) -> list[dict]:
        return [t.definition() for t in self]

    def openai_schemas(self, names: Iterable[str] | None = None) -> list[dict]:
        """Every tool's schema, or only the named ones (unknown names are ignored)."""
        if names is None:
            return [t.openai_schema() for t in self]
        return [self._tools[n].openai_schema() for n in dict.fromkeys(names) if n in self._tools]

    def call(self, session: Session, name: str, arguments: Mapping[str, Any] | None) -> Any:
        tool = self.get(name)
        return tool.handler(session, **validate_arguments(tool.parameters, arguments))
