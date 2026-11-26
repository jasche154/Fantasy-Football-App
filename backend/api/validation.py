"""422 answers for request bodies the schema rejects, safe for any input: the default handler echoes the value, and
answered 500 on NaN or Infinity (JSON can't encode them), a lone surrogate (UTF-8 can't), and echoed huge or deeply
nested inputs back whole."""
import math
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

NOT_FINITE = "Input should be a finite number"
NON_FINITE_WORDS = {"nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"}
MAX_DEPTH = 20                              # an echoed input nested deeper reads "..." (no RecursionError)
MAX_TEXT = 200                              # characters of an echoed string
MAX_ITEMS = 5                               # items of an echoed list or dict
NUMBER_ERRORS = {"float_parsing", "float_type", "int_parsing", "int_type", "int_from_float", "finite_number",
                 "less_than", "less_than_equal", "greater_than", "greater_than_equal", "multiple_of"}


def _not_finite(value: Any) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    return isinstance(value, str) and value.strip().lower() in NON_FINITE_WORDS


def _safe(value: Any, depth: int = 0) -> Any:
    """`value` made safe to echo: non-finite floats as text, strings cut to MAX_TEXT characters with lone surrogates
    replaced (they can't be encoded), lists and dicts cut to MAX_ITEMS items, nothing deeper than MAX_DEPTH."""
    if depth > MAX_DEPTH:
        return "..."
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, str):
        text = value if len(value) <= MAX_TEXT else value[:MAX_TEXT] + "..."
        return text.encode("utf-8", "replace").decode("utf-8")
    if isinstance(value, dict):
        items = list(value.items())
        kept = {_safe(str(key), depth + 1): _safe(item, depth + 1) for key, item in items[:MAX_ITEMS]}
        return {**kept, "...": f"{len(items) - MAX_ITEMS} more"} if len(items) > MAX_ITEMS else kept
    if isinstance(value, (list, tuple)):
        kept = [_safe(item, depth + 1) for item in value[:MAX_ITEMS]]
        return [*kept, "..."] if len(value) > MAX_ITEMS else kept
    return value


def safe_errors(errors: list[dict]) -> list[dict]:
    """The validation errors, JSON-safe, each one about a NaN or Infinity input saying so."""
    found = []
    for error in errors:
        error = dict(error)
        if error.get("type") in NUMBER_ERRORS and _not_finite(error.get("input")):    # not a Literal's "inf"
            error["msg"], error["type"] = NOT_FINITE, "finite_number"
            error.pop("ctx", None)
        found.append(_safe(error))
    return found


async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse({"detail": jsonable_encoder(safe_errors(list(exc.errors())))}, status_code=422)
