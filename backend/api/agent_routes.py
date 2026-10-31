"""Agent tools (callable by any agent framework) and the built-in AI assistant."""
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from config import get_settings
from database.db import get_session
from services.agent import AgentUnavailable, run_agent
from services.agent_suggestions import suggest
from services.llm_client import build_llm_client
from services.my_team import my_leagues
from services.players import AmbiguousPlayer, PlayerNotFound
from services.projections import latest_season, latest_week
from services.valuation import NoDataError
from tools.definitions import TOOLS, sanitize_for_agent
from tools.registry import ToolError, ToolUnavailable

router = APIRouter(prefix="/api")

MAX_USER_CHARS = 4_000
MAX_ASSISTANT_CHARS = 12_000      # replies can be long; the browser sends them back as history
NO_LLM = ("The AI assistant needs an AI provider key in backend/.env: GROQ_API_KEY, "
          "or another provider listed in LLM_CHAIN.")
ERROR_STATUS = {"rate_limit": 429, "too_large": 413, "provider": 502}
TOO_LARGE = "That question needs more data than the AI provider accepts at once. Ask a narrower question."


class ToolRequest(BaseModel):
    arguments: dict[str, Any] = {}


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=MAX_ASSISTANT_CHARS)

    @model_validator(mode="after")
    def _user_limit(self) -> "ChatMessage":
        if self.role == "user" and len(self.content) > MAX_USER_CHARS:
            raise ValueError(f"A question can be at most {MAX_USER_CHARS} characters.")
        return self


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(..., min_length=1, max_length=30)
    # Facts the assistant saved with its remember tool; the browser keeps them for the chat.
    memory: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(default_factory=list, max_length=10)
    league_id: int | None = None      # optional league this chat is about


@router.get("/agent/tools")
def agent_tools() -> dict:
    return {"tools": TOOLS.definitions()}


@router.post("/agent/tools/{name}")
def agent_tool_call(name: str, request: ToolRequest, session: Session = Depends(get_session)) -> dict:
    try:
        TOOLS.get(name)
    except ToolError as exc:
        raise HTTPException(404, str(exc)) from exc
    try:
        return {"result": sanitize_for_agent(TOOLS.call(session, name, request.arguments))}
    except (PlayerNotFound, AmbiguousPlayer) as exc:
        raise HTTPException(404, str(exc)) from exc
    except NoDataError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ToolUnavailable as exc:
        raise HTTPException(502, str(exc)) from exc
    except ValueError as exc:   # ToolError and service-level validation
        raise HTTPException(422, str(exc)) from exc


def _unavailable(exc: AgentUnavailable) -> HTTPException:
    status = ERROR_STATUS.get(getattr(exc, "kind", "provider"), 502)
    headers = {"Retry-After": str(exc.retry_after or 60)} if status == 429 else None
    return HTTPException(status, TOO_LARGE if status == 413 else str(exc), headers=headers)


@router.post("/agent/chat")
def agent_chat(request: ChatRequest, session: Session = Depends(get_session)) -> dict:
    leagues = my_leagues(session)
    if request.league_id is not None and request.league_id not in {league.id for league, _ in leagues}:
        raise HTTPException(422, "That league isn't imported.")
    client = build_llm_client(get_settings())
    if client is None:
        raise HTTPException(503, NO_LLM)
    try:
        result = run_agent(session, client, [m.model_dump() for m in request.messages],
                           memory=request.memory, league_id=request.league_id)
    except AgentUnavailable as exc:
        raise _unavailable(exc) from exc
    season = latest_season(session)
    names = [t.name for t in result.tool_calls]
    return {"reply": result.reply,
            "tool_calls": [{"name": t.name, "arguments": dict(t.arguments), "ok": t.ok} for t in result.tool_calls],
            "memory": list(result.memory), "stop_reason": result.stop_reason, "unverified": list(result.unverified),
            "provider": result.provider,
            "data_as_of": {"season": season, "week": latest_week(session, season) if season else None},
            "suggestions": suggest(names, bool(leagues))}
