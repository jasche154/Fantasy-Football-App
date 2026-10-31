"""Fit tool results into a model's character budget without ever sending broken JSON.

`compact` drops empty values and rounds floats. `fit` then shortens lists from the end (top rows
are kept, since every tool sorts best-first) and says what it cut, so the model knows a list is
partial instead of reading a half-cut string.
"""
import json
from collections.abc import Sequence
from typing import Any

FLOAT_DIGITS = 2
LONG_TEXT = 200                    # strings are only shortened as a last step, after lists
TRUNCATED_HINT = "pass position/limit/league to narrow"


def compact(value: Any) -> Any:
    """A copy without None, [] or {} (recursively), with floats rounded to 2 decimals."""
    if isinstance(value, dict):
        items = ((k, compact(v)) for k, v in value.items())
        return {k: v for k, v in items if not _empty(v)}
    if isinstance(value, (list, tuple)):
        return [v for v in (compact(v) for v in value) if not _empty(v)]
    if isinstance(value, float):
        return round(value, FLOAT_DIGITS)
    return value


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, (list, dict)) and not value)


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _lists(data: dict, prefix: str = "") -> dict[str, list]:
    """Every list reachable through dicts (not inside lists), keyed by dotted path."""
    found: dict[str, list] = {}
    for key, value in data.items():
        path = f"{prefix}{key}"
        if isinstance(value, list):
            found[path] = value
        elif isinstance(value, dict):
            found.update(_lists(value, f"{path}."))
    return found


def _replace(data: dict, path: str, value: Any) -> dict:
    """A copy of `data` with the dotted path set to `value` (the input is never changed)."""
    head, _, rest = path.partition(".")
    return {**data, head: _replace(data[head], rest, value) if rest else value}


def _marker(path: str, shown: int, total: int, also: Sequence[str]) -> dict:
    marker = {"shown": shown, "total": total, "list": path, "hint": TRUNCATED_HINT}
    return {**marker, "also_trimmed": list(also)} if also else marker


def _with_marker(data: dict, trimmed: list[tuple[str, int, int]]) -> dict:
    path, shown, total = trimmed[0]
    return {**data, "truncated": _marker(path, shown, total, [p for p, _, _ in trimmed[1:]])}


def _longest_prefix(data: dict, path: str, items: list, trimmed: list, budget: int) -> int:
    """The most leading items of one list that fit (binary search); 0 if even one doesn't."""
    low, high = 0, len(items)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = _with_marker(_replace(data, path, items[:middle]), [*trimmed, (path, middle, len(items))])
        if len(dumps(candidate)) <= budget:
            low = middle
        else:
            high = middle - 1
    return low


def _shorten_text(value: Any) -> Any:
    if isinstance(value, str) and len(value) > LONG_TEXT:
        return value[:LONG_TEXT - 1] + "…"
    if isinstance(value, dict):
        return {k: _shorten_text(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_shorten_text(v) for v in value]
    return value


def _last_resort(trimmed: list[tuple[str, int, int]], budget: int) -> str:
    options = []
    if trimmed:
        options.append(dumps({"error": "result too large", "truncated": _with_marker({}, trimmed)["truncated"]}))
    options += [dumps({"error": "result too large"}), "{}"]
    return next(text for text in options if len(text) <= budget or text == "{}")


def fit(value: Any, budget_chars: int, trim_keys: Sequence[str] = ()) -> str:
    """`value` as JSON of at most `budget_chars` characters, always valid.

    Lists are cut from the end: `trim_keys` first (in order), then the largest remaining list.
    A cut adds a top-level "truncated" note; a top-level list is wrapped as {"items": [...]}.
    """
    data = compact(value)
    text = dumps(data)
    if len(text) <= budget_chars:
        return text
    if not isinstance(data, dict):
        data = {"items": data} if isinstance(data, list) else {"value": data}
        trim_keys = ("items", *trim_keys)
    trimmed: list[tuple[str, int, int]] = []
    tried: set[str] = set()
    while True:
        lists = {p: v for p, v in _lists(data).items() if p not in tried and len(v) > 0}
        if not lists:
            break
        path = next((k for k in trim_keys if k in lists), None) or max(lists, key=lambda p: len(dumps(lists[p])))
        tried.add(path)
        items = lists[path]
        shown = _longest_prefix(data, path, items, trimmed, budget_chars)
        trimmed.append((path, shown, len(items)))
        data = _replace(data, path, items[:shown])
        if len(dumps(_with_marker(data, trimmed))) <= budget_chars:
            return dumps(_with_marker(data, trimmed))
    shortened = _shorten_text(data)
    candidate = _with_marker(shortened, trimmed) if trimmed else shortened
    if len(dumps(candidate)) <= budget_chars:
        return dumps(candidate)
    return _last_resort(trimmed, budget_chars)
