# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Gridiron Edge: a single-user, localhost fantasy football app. FastAPI + SQLAlchemy 2 + SQLite backend (`backend/`), React 19 + TypeScript + Vite frontend (`frontend/`). README.md documents every feature and how each number is computed; keep it in sync when behavior changes.

## Commands

Backend (run from `backend/`, Windows venv at `backend/venv`):
```bash
venv/Scripts/python.exe -m pytest -q                                  # full suite (~2-4 min)
venv/Scripts/python.exe -m pytest -q tests/test_league_moves.py       # one file
venv/Scripts/python.exe -m pytest -q tests/test_league_moves.py -k faab   # one test by name
venv/Scripts/python.exe -m uvicorn main:app --reload                  # API on :8000 (docs at /docs)
venv/Scripts/python.exe -m database.create_tables                     # create/upgrade schema (idempotent migrations)
venv/Scripts/python.exe -m database.populate_stats                    # load nflverse data (also relearns source weights)
venv/Scripts/python.exe -m alembic upgrade head                       # apply migrations; `alembic check` should report no diff
venv/Scripts/python.exe -m services.weekly_refresh [--week N] [--force]   # pull Yahoo/ESPN/props for a week
venv/Scripts/python.exe -m services.backup                            # back up the SQLite DB
```
Model/evaluation CLIs (all point-in-time, no look-ahead): `services.backtest`, `services.score_ranges --seasons 2021-2025` (refits floor/ceiling quantiles), `services.projection_factors`, `services.weight_learning --season 2026 [--dry-run]`, `services.history_backfill --season 2026 --weeks 1-6`, `services.projection_eval`, `services.agent_eval [--provider groq] [--split holdout] [--compare a.json b.json]` (live Ask AI eval, needs a provider key; reports go to `backend/eval_runs/`, git-ignored).

Frontend (run from `frontend/`): `npm run dev` (:5173), `npm test` (vitest), `npx vitest run src/pages/team` (one folder), `npm run build` (runs `tsc` then Vite; the typecheck is the only static check, there is no ESLint config).

CI (`.github/workflows`) runs `pytest -q --cov` and `npm test && npm run build` on every PR.

## Architecture

**Layers.** `api/*_routes.py` are thin routers; logic lives in `services/` (pure, tested); outside HTTP clients live in `integrations/` (Yahoo OAuth + fantasy API, Yahoo expert ranks, ESPN, The Odds API, Sleeper, weather). Tests use an in-memory SQLite with seeded data (`tests/conftest.py`: `session`, `seeded`, `client` fixtures; `isolated_state` clears settings and module caches between tests). `database/models/` is a package split by area; import models from `database.models`.

**Data flow for a week's projection** (`services/weekly_rankings.build_weekly_rankings`):
1. `build_valuation` (services/valuation.py) builds rest-of-season values for a `LeagueSettings` (cached per settings + data version).
2. Outside sources are read from the `weekly_source_projections` table (never fetched during a build).
3. `blend()` mixes four groups (props, experts = Yahoo consensus + Josh & Hayden, ESPN, the app's model) with weights from `weight_learning.effective_weights(season, before_week)`. Weights are learned point-in-time, so past weeks never use hindsight.
4. `weekly_adjust.adjust` applies injuries (`services/availability.py` for chance to play, `services/injuries.py` for vacated-work boosts), and ranges from `score_ranges.player_quantiles` (empirical quantiles; floor/ceiling are q10/q90 *if he plays*; play probability is separate).
5. The result is cached in memory and as a signed pickle in `rankings_snapshots` (`services/rankings_store.py`, key includes a hash of the model files in `MODEL_FILES`); pre-kickoff projections are saved to `projection_history`, which is what weight learning and accuracy reports use.

**User's Yahoo leagues** (`services/my_team.py` is the hub: `find_league`, `league_settings`, `rosters`, `build_my_team`):
- `matchup.py` + `matchup_sim.py`: correlated (Gaussian copula) head-to-head simulation; upside/safe only recommended with a 3-point win-probability edge.
- `lineup_gain.py`: the shared "points added to your best lineup over the rest of the season" engine used by `league_moves.py` (waiver plan with named drop, trade scoring on both rosters), with `waiver_advice.py` branching on league type (FAAB / rolling priority / free agency).
- `league_insights.py` (standings, tendencies, bid history from Yahoo; cached 1 h) and `season_sim.py` (playoff odds: team offsets + starter injury chain).
- `playoffs.py`, `streaming.py`, `game_locks.py` (kickoff locks, late FLEX, warnings; kickoffs are US Eastern in nflverse, converted in `schedule_strength.Game.kickoff`).

**Concurrency/caching.** Expensive builds use `services/build_locks.key_lock(key)` so parallel requests wait for one build. Module-level caches (valuation, weekly rankings, matchup, league insights, Yahoo schedule) must be cleared in tests via `conftest._clear_caches`. `services/auto_update.py` runs a background thread: stats loads, Yahoo re-syncs, pre-game snapshots and daily backups every 6 h, plus a 15-minute game-day props check (props are bought once per game within 2 h of kickoff).

**Ask AI.** `services/agent.py` runs a tool-calling loop through `services/llm_client.py` (OpenAI-compatible httpx client; `LLM_CHAIN` picks free providers in order, default Groq; `FallbackClient` moves on after rate limits). The prompt is built in `services/agent_prompt.py`. Tools live in `tools/tool_specs.py` (names, descriptions, schemas), `tools/definitions.py`, `tools/league_tools.py`, `tools/trade_tools.py`, `tools/start_sit_tool.py`, `tools/injury_tools.py` (schema-validated by `tools/registry.py`, also served by `mcp_server.py`); results go through `tools/shaping.fit` (always valid JSON within the provider's budget) and agent rows come from `tools/rows.py`. `services/agent_routing.py` sends only the tools a question needs, using `services/player_index.py` for names, nicknames and typos; add keywords there (and a case in `services/eval_cases.py`) when adding a tool, and add new tools to `READ_ONLY_TOOLS` in `tests/test_tool_safety.py` and to `services/agent_suggestions.py`. `services/grounding.py` checks every answer's players and numbers against tool results (one revision, then a caveat). Text from Yahoo or news is data, never instructions: wrap names with `services/labels.label` and free text with `labels.untrusted`, and `remember` only accepts facts that restate the user's own words. Offline agent tests use `tests/agent_fakes.ScriptedLLM`.

**Security model.** Localhost only: Host allowlist, CORS, cross-site requests (by Origin and `Sec-Fetch-Site`) rejected, non-loopback clients rejected unless `API_TOKEN` is set (`main.py`). Secrets live in `backend/.env` (never print them); Yahoo tokens are encrypted at rest; error messages and stored statuses carry exception types only, because some URLs contain API keys.

**Frontend.** Hash router (`src/router/routes.ts`, `App.tsx`), API calls in `src/api/data.ts` / `actions.ts` through `src/api/client.ts` (adds the optional API token). Types live in `src/api/types/*` (import from `src/api/types`). Data loading uses `useAsync`; pages compose small views (e.g. `pages/team/*View.tsx`). Styles are plain CSS per area in `src/styles/`, using theme tokens from `tokens.css` (light and dark).

## Conventions

- Schema changes need an Alembic migration that is idempotent (check `inspect(...).has_table` / existing indexes first); the local DB isn't committed.
- Model changes should be judged out of sample (leave-season-out, or point-in-time rebuilds), and on RMSE, rank correlation or decision metrics, not in-sample MAE.
- Source files stay under ~400 lines (800 hard ceiling); split by area and re-export to keep import paths stable.
- `backend/analysis_output/` and `backend/package-lock.json` are the user's untracked files: never stage them.
