"""FastAPI entrypoint. Run from backend/:  uvicorn main:app --reload"""
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from api import data_routes, legacy_routes, market_routes
from config import get_settings
from database.db import SessionLocal
from services.league import LeagueSettings
from services.valuation import build_valuation

log = logging.getLogger("fantasy_app")
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _warm_cache() -> None:
    """Compute default-league values in the background so the first page load is fast."""
    try:
        with SessionLocal() as session:
            build_valuation(session, LeagueSettings())
    except Exception:  # warming is best-effort; requests compute values on demand anyway
        log.exception("Could not pre-compute player values")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    threading.Thread(target=_warm_cache, daemon=True).start()
    yield


def create_app(warm_cache: bool = True) -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Fantasy Football Toolkit", version="1.0.0",
                  lifespan=lifespan if warm_cache else None)

    # The API is unauthenticated and meant for localhost. Rejecting other Host headers stops
    # DNS-rebinding pages from becoming same-origin with it.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.middleware("http")
    async def reject_cross_site_writes(request: Request, call_next):
        # Browsers always send Origin on cross-site POSTs (even "no-cors" ones); tools like
        # curl or the MCP server send none, so they still work.
        origin = request.headers.get("origin")
        if request.method not in SAFE_METHODS and origin and origin not in settings.cors_origins:
            return JSONResponse({"detail": "Cross-site request blocked."}, status_code=403)
        return await call_next(request)

    for module in (data_routes, market_routes, legacy_routes):
        app.include_router(module.router)
    return app


app = create_app()
