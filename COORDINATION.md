# Coordination

Parallel threads list their task and the files they plan to edit. Don't edit a file another
active thread has claimed; ask first.

## fix/ask-ai-rate-limits (branch fix/ask-ai-rate-limits)

Fix the Ask AI feature: free-tier rate limit hit inside one question, untrimmed history, trade
routing and trade tool failures.

Files:
- `backend/services/agent.py`: wait out short rate limits and retry; fit chat history into the provider budget
- `backend/services/llm_client.py` (`FallbackClient.profile` only): size requests for the provider that answers next
- `backend/services/agent_routing.py`: keep each topic's main tools under the tool cap; trade typo
- `backend/tools/trade_tools.py`: evaluate_trade splits players from different teams into one trade per team
- `backend/tools/league_tools.py` (`league_trades` only): explain an empty trade list
- `backend/tools/tool_specs.py` (`evaluate_trade` description only)
- `backend/tests/conftest.py`: make rate-limit waits instant in tests
- `frontend/src/pages/ask/ChatError.tsx`: name a daily cap as such, with hours/minutes
- tests: `backend/tests/test_agent*.py`, `backend/tests/test_agent_routing*.py`, `backend/tests/test_trade_tools*.py`, `frontend/src/pages/ask/*.test.tsx`
- `README.md` (Ask AI section only)
