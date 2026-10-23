"""Third-party integrations: status, Yahoo league import, Sleeper trending."""
import logging
from typing import Literal
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from config import get_settings
from database.db import get_session
from integrations.sleeper import SleeperError, trending_players
from integrations.token_crypto import TokenCryptoError
from integrations.yahoo import YahooAuthError, YahooError
from services import yahoo_sync

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")
HTTP_TIMEOUT_SECONDS = 15.0
YAHOO_ERRORS = (yahoo_sync.YahooNotConfigured, TokenCryptoError, YahooError, LookupError)


class YahooExchange(BaseModel):
    code: str = Field(..., min_length=1, max_length=2048)


def integration_status(session: Session) -> dict:
    settings = get_settings()
    connection = yahoo_sync.get_connection(session)
    return {"fantasypros": bool(settings.fantasypros_api_key),
            "yahoo_configured": bool(settings.yahoo_client_id and settings.yahoo_client_secret),
            "yahoo_connected": bool(connection and connection.access_token)}


@router.get("/integrations/status")
def integrations_status(session: Session = Depends(get_session)) -> dict:
    return integration_status(session)


@router.get("/insights/trending")
def trending(type: Literal["add", "drop"] = "add", limit: int = Query(10, ge=1, le=25),
             session: Session = Depends(get_session)) -> dict:
    try:
        return {"results": trending_players(session, type, limit)}
    except SleeperError as exc:
        raise HTTPException(502, "Sleeper trending data is unavailable right now.") from exc


def _yahoo_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (yahoo_sync.YahooNotConfigured, TokenCryptoError)):
        return HTTPException(503, str(exc))
    if isinstance(exc, YahooAuthError):
        return HTTPException(401, str(exc))
    if isinstance(exc, LookupError):
        return HTTPException(404, str(exc))
    log.warning("Yahoo request failed: %s", exc)
    return HTTPException(502, str(exc))


def parse_code(raw: str) -> tuple[str, str | None]:
    """Accept either the bare code or the whole redirected URL (…/callback?code=…&state=…)."""
    text = raw.strip()
    if "code=" not in text:
        return text, None
    query = parse_qs(urlparse(text).query if "?" in text else text)
    code = (query.get("code") or [""])[0]
    if not code:
        raise HTTPException(422, "Couldn't find a code in that URL.")
    return code, (query.get("state") or [None])[0]


@router.get("/integrations/yahoo/auth-url")
def yahoo_auth_url() -> dict:
    try:
        return {"url": yahoo_sync.start_authorization(get_settings())}
    except YAHOO_ERRORS as exc:
        raise _yahoo_http_error(exc) from exc


@router.post("/integrations/yahoo/exchange")
def yahoo_exchange(request: YahooExchange, session: Session = Depends(get_session)) -> dict:
    code, state = parse_code(request.code)
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as http:
            yahoo_sync.connect(session, get_settings(), http, code, state)
    except YAHOO_ERRORS as exc:
        raise _yahoo_http_error(exc) from exc
    return {"connected": True}


@router.get("/integrations/yahoo/leagues")
def yahoo_leagues(session: Session = Depends(get_session)) -> dict:
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as http:
            leagues = yahoo_sync.list_leagues(session, get_settings(), http)
    except YAHOO_ERRORS as exc:
        raise _yahoo_http_error(exc) from exc
    return {"leagues": leagues}


@router.post("/integrations/yahoo/leagues/{league_key}/import")
def yahoo_import(league_key: str, session: Session = Depends(get_session)) -> dict:
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as http:
            return yahoo_sync.import_league(session, get_settings(), http, league_key)
    except YAHOO_ERRORS as exc:
        raise _yahoo_http_error(exc) from exc
