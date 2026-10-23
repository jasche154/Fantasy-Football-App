"""Manual end-to-end check of the AI assistant against Groq (needs GROQ_API_KEY).

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
from services.agent import GroqClient, run_agent


def main() -> None:
    question = " ".join(sys.argv[1:]) or "How is rushing efficiency looking this season?"
    settings = get_settings()
    if not settings.groq_api_key:
        raise SystemExit("Set GROQ_API_KEY in backend/.env first.")
    with SessionLocal() as session:
        reply = run_agent(session, GroqClient(settings.groq_api_key, settings.groq_model),
                          [{"role": "user", "content": question}])
    for call in reply.tool_calls:
        print(f"[tool] {call.name} {dict(call.arguments)} ok={call.ok}")
    print(reply.reply)


if __name__ == "__main__":
    main()
