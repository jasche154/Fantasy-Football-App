# Coordination

Parallel threads list their task and the files they plan to edit. Don't edit a file another
active thread has claimed; ask first.

## feat/redraft-phase1-2 (branch feat/redraft-phase1-2)

GWTTKB redraft ideas, phases 1 and 2 (`docs/plans/gwttkb-redraft-ideas.md`), plus a read-only Odds page.

Files:
- League: `backend/services/{all_play,manager_tags,trade_grades,league_scoreboards,league_insights}.py`,
  `backend/tools/league_tools.py`, `backend/tools/tool_specs.py` (get_power_rankings), `frontend/src/pages/team/{LeagueView,PastTradesView}.tsx`
- Ask AI: `backend/services/{grounding,grounding_roster,agent,agent_direct,agent_prompt,eval_cases}.py`,
  `backend/api/agent_routes.py`, `frontend/src/pages/ask/*`
- Trades/waivers: `backend/services/{trade,serialize,league_trades,league_moves,waiver_advice,valuation,replacement_eval}.py`,
  `backend/api/team_routes.py`, `frontend/src/pages/{trade,waivers}/*`, `frontend/src/pages/team/{TradesView,WaiversView}.tsx`
- Projections: `backend/services/{threshold_odds,threshold_calibration,props_calibration,score_ranges,weekly_rankings,history_backfill,rankings_store}.py`,
  `frontend/src/pages/weekly/*`
- Player/Odds: `backend/services/{player_percentiles,player_usage,odds_board}.py`, `backend/api/{odds_routes,player_extra_routes}.py`,
  `backend/main.py`, `frontend/src/pages/{odds,player}/*`, router/nav
- Shared: `frontend/src/api/{data.ts,types.ts,types/*}`, `backend/tests/conftest.py`, `README.md`, `docs/API.md`
