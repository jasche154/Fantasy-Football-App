"""Agent tools (callable by any agent framework) and the built-in AI assistant."""
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from config import get_settings
from database.db import get_session
from services.agent import AgentUnavailable, GroqClient, run_agent
from services.players import AmbiguousPlayer, PlayerNotFound
from services.valuation import NoDataError
from tools.definitions import TOOLS, sanitize_for_agent
from tools.registry import ToolError, ToolUnavailable

router = APIRouter(prefix="/api")


class ToolRequest(BaseModel):
    arguments: dict[str, Any] = {}


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(..., min_length=1, max_length=30)
    # Facts the assistant saved with its remember tool; the browser keeps them for the chat.
    memory: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(default_factory=list, max_length=10)


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


@router.post("/agent/chat")
def agent_chat(request: ChatRequest, session: Session = Depends(get_session)) -> dict:
    settings = get_settings()
    if not settings.groq_api_key:
        raise HTTPException(503, "The AI assistant needs GROQ_API_KEY in backend/.env.")
    try:
        result = run_agent(session, GroqClient(settings.groq_api_key, settings.groq_model),
                           [m.model_dump() for m in request.messages], memory=request.memory)
    except AgentUnavailable as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"reply": result.reply, "tool_calls": [
        {"name": t.name, "arguments": dict(t.arguments), "ok": t.ok} for t in result.tool_calls],
        "memory": list(result.memory)}
