"""422 answers for request bodies the schema rejects, safe for any input (the default handler 500s on NaN or
Infinity: it echoes the value and JSON can't encode it)."""
import math
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

NOT_FINITE = "Input should be a finite number"
NON_FINITE_WORDS = {"nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"}


def _not_finite(value: Any) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    return isinstance(value, str) and value.strip().lower() in NON_FINITE_WORDS


def _safe(value: Any) -> Any:
    """`value` with every non-finite float written as text."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {key: _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    return value


def safe_errors(errors: list[dict]) -> list[dict]:
    """The validation errors, JSON-safe, each one about a NaN or Infinity input saying so."""
    found = []
    for error in errors:
        error = dict(error)
        if _not_finite(error.get("input")):
            error["msg"], error["type"] = NOT_FINITE, "finite_number"
            error.pop("ctx", None)
        found.append(_safe(error))
    return found


async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse({"detail": jsonable_encoder(safe_errors(list(exc.errors())))}, status_code=422)
