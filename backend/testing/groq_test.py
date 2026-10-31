"""Manual end-to-end check of the AI assistant against the configured provider chain (LLM_CHAIN).

Run from backend/:
    python -m testing.groq_test "Who are the best buy-low running backs right now?"
"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    # Run by file path (`python testing/groq_test.py`): make backend/ importable.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import get_settings  # noqa: E402
from database.db import SessionLocal
from services.agent import run_agent
from services.llm_client import build_llm_client


def main() -> None:
    question = " ".join(sys.argv[1:]) or "How is rushing efficiency looking this season?"
    settings = get_settings()
    client = build_llm_client(settings)
    if client is None:
        raise SystemExit("Set GROQ_API_KEY (or another provider in LLM_CHAIN) in backend/.env first.")
    with SessionLocal() as session:
        reply = run_agent(session, client, [{"role": "user", "content": question}])
    for call in reply.tool_calls:
        print(f"[tool] {call.name} {dict(call.arguments)} ok={call.ok}")
    print(f"[{reply.provider}] {reply.stop_reason}, {reply.rounds} requests, unverified={list(reply.unverified)}")
    print(reply.reply)


if __name__ == "__main__":
    main()
