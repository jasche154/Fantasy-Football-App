# Overhaul Plan — Fantasy Football App

Goal: turn the current prototype (one rushing leaderboard + a Groq tool test) into a
FantasyPros / Flock-style toolkit: trade calculator, buy-low / sell-high finder,
rankings, advanced-stat leaderboards, player pages, and an AI assistant whose
answers come from tool calls against real data.

The README's core rule still holds: **numbers come from data and deterministic
code; the LLM only explains them.** Every output is labeled as one of *actual
stat*, *expected stat* (opportunity model from nflverse), *projection* (our
model), or *expert ranking* (FantasyPros ECR).

---

## 1. Issues found in the current code

| # | Severity | Where | Problem |
|---|---|---|---|
| 1 | HIGH | `tools/rushleaders.py` | nflverse Next Gen Stats include a **week 0 row = season total**. The leaderboard averages it in with the weekly rows, and the `min_attempts` filter sums it twice (Derrick Henry 2025: 307 attempts counted as 614). |
| 2 | HIGH | `tools/rushleaders.py` | Season values are an **unweighted mean of weekly rates** (a 5-carry week counts as much as a 30-carry week). Henry 2025 shows 1.11 RYOE/att; true season value is 1.05. |
| 3 | HIGH | `database/populate_stats.py` | Only inserts rows for the *current* season (`if row["season"] not in CURRENT_SEASONS: continue`), so a fresh database can never get 2021-2025 data. It also downloads all 6 seasons each run just to throw 5 away. |
| 4 | HIGH | `player_expected_points_weekly` | Table is empty (0 rows) and its Alembic migration `11919bb26697` is an empty `pass`; `alembic_version` is stuck at the previous revision. |
| 5 | MEDIUM | `populate_stats.py` | One `SELECT` per stat row to find the player (~100k queries per run). Player team/position is frozen at first sight and never updated. |
| 6 | MEDIUM | `README.md` | `uvicorn main:app` fails (app lives in `testing/main.py`); `cd database && python create_tables.py` fails (`ModuleNotFoundError: database`); README says React but the frontend is vanilla TS; install line lacks several real deps; no `requirements.txt`. |
| 7 | MEDIUM | `testing/main.py` | Errors are returned with HTTP 200 (`{"error": ...}`); no validation (`limit=-1` returns every row); bare `except Exception` returns internal error text to the client. |
| 8 | MEDIUM | `database/models.py` | No unique constraint on `players.gsis_id`; no indexes on `(player_id, season, week)`; `datetime.utcnow` is deprecated. |
| 9 | MEDIUM | `frontend/src/main.ts` | Player names written with `innerHTML` (XSS-prone pattern); API base URL hardcoded; metric list duplicated from the backend; season input capped at 2026. |
| 10 | LOW | `tools/rushleaders.py` | Query executed twice; metric named `rush_yards_over_expected` actually returns the *per-attempt* column; unused imports. |
| 11 | LOW | `testing/groq_test.py` | Crashes if the model answers without a tool call; only handles one tool call / one round. |
| 12 | LOW | repo | `backend/data/` (512 MB Big Data Bowl zip) isn't ignored and would be rejected by GitHub; unused Vite template files (`counter.ts`, `style.css`, `hero.png`). No tests anywhere. |
| 13 | LOW | `.env` | Yahoo redirect URI is `https://localhost/callback` (no port, HTTPS), which the FastAPI dev server can't receive, so the OAuth flow needs a "paste the code" step. |

## 2. Data sources (details in `docs/DATA_SOURCES.md`)

Free and already wired in through `nflreadpy` (no key):

- `load_player_stats`: weekly box score, EPA, CPOE, target share, WOPR, fantasy points
- `load_nextgen_stats`: passing / rushing / receiving NGS (RYOE, separation, cushion, xYAC, time to throw)
- `load_ff_opportunity`: **expected fantasy points** per player-week. This drives buy-low / sell-high.
- `load_snap_counts`: offensive snap share
- `load_ff_playerids`: ID crosswalk (gsis ↔ yahoo ↔ sleeper ↔ fantasypros ↔ pfr ↔ espn)
- `load_ff_rankings`: FantasyPros weekly and redraft ECR scraped by DynastyProcess (free)

Keyed or optional: FantasyPros API (your key, for ROS ECR and projections), Yahoo Fantasy
API (your OAuth app, for league import), Sleeper public API (trending adds/drops, no key),
and Groq (already configured, for the assistant).

## 3. Architecture

```
backend/
  main.py                 FastAPI app (the README already points here)
  config.py               pydantic-settings; reads .env
  api/                    routers: meta, players, rankings, leaderboards, trade, insights, agent, integrations
  services/               pure, tested business logic
    scoring.py            ScoringSettings + fantasy points from raw stats (verified vs nflverse on 99,183 rows)
    league.py             LeagueSettings + replacement levels
    projections.py        season summaries + rest-of-season points-per-game model
    valuation.py          VORP -> trade value
    trade.py              trade evaluation + "balance this trade" suggestions
    insights.py           buy-low / sell-high signals with plain-English reasons
    leaderboards.py       whitelisted, volume-weighted advanced-stat leaderboards
    players.py            search / name resolution / profile
    agent.py              LLM tool-calling loop (Groq), provider-injectable for tests
  integrations/           fantasypros.py, yahoo.py, sleeper.py, token_crypto.py
  tools/                  agent tool registry (JSON-schema tools wrapping services)
  database/               models, db, create_tables, populate_stats (rewritten)
  tests/                  pytest; seeded in-memory SQLite, never the real DB
frontend/                 React + TypeScript + Vite (matches the README's stated stack)
```

## 4. Feature design

### Scoring
Presets `std` / `half` / `ppr` plus overrides (pass TD points, TE premium). Points are
computed from raw stat columns, so custom scoring works on any week. Expected points from
`ff_opportunity` are PPR-based; they're converted to other scoring with
`xFP - (1 - rec_pts) * receptions_exp`.

### Rest-of-season projection (labeled "model")
For each QB/RB/WR/TE:
`current = 0.6 * actual_ppg + 0.4 * expected_ppg` (opportunity is stickier than production),
then shrink toward last season's PPG by games played:
`ros_ppg = w * current + (1 - w) * prior`, where `w = games / (games + 4)`. With no prior
season, it shrinks toward the positional replacement level instead.

### Value and trade calculator
- Replacement level per position comes from league settings (teams × starters, FLEX and
  superflex filled greedily, plus each position's share of the bench).
- `trade_value = max(0, ros_ppg - replacement_ppg) × weeks_remaining`, in ROS fantasy
  points above a waiver pickup.
- Packages: each side is sorted by value and weighted `1.0, 0.9, 0.8, ...` (floor 0.5).
  This consolidation discount means two mid players don't automatically equal one stud.
  The weights are returned in the response so the UI can explain them.
- Verdict from the percent difference: ≤5% fair, ≤15% slight edge, otherwise lopsided.
- "Balance it" suggests players not in the deal whose addition brings the gap closest to 0.

### Buy low / sell high
Per player this season (minimum 3 games):
- FPOE/game = actual − expected fantasy points per game
- TD luck = actual TDs − expected TDs
- usage trend = last-3-game expected points (and snap %) vs. the season average
- recent form = last-3 PPG vs. season PPG (a proxy for what other managers react to)

Buy-low candidates have a valuable role (expected PPG above replacement), negative FPOE,
and stable or rising usage. Sell-high is the mirror image. Each candidate comes with
generated reasons, e.g. "Scoring 3.9 fewer PPR pts/game than usage predicts
(11.2 vs 15.1)."

### Leaderboards
A whitelisted metric catalog (rushing / receiving / passing NGS plus usage/EPA from
weekly stats). Metrics are **volume-weighted** (Σ RYOE / Σ attempts, attempt-weighted
rates) and exclude NGS week-0 total rows. The old `/api/rankings/rushing` endpoint stays
and is fixed.

### Agent tools
A registry of JSON-schema tools: `search_players`, `get_player_profile`,
`get_player_game_log`, `compare_players`, `get_fantasy_rankings`, `get_leaderboard`,
`list_leaderboard_metrics`, `evaluate_trade`, `get_buy_low_candidates`,
`get_sell_high_candidates`, `get_expert_rankings`, `get_rushing_leaders`. Arguments are
validated before dispatch, and players can be referenced by name (fuzzy matched) or id.
They're exposed three ways:
1. `GET /api/agent/tools` and `POST /api/agent/tools/{name}` (any agent or framework)
2. `POST /api/agent/chat`: a multi-round Groq tool loop that returns the answer plus a tool-call trace
3. An MCP server (`python -m mcp_server`) so Claude Desktop / Claude Code can use the tools directly

### Integrations
- **FantasyPros API**: client for ECR and projections when `FANTASYPROS_API_KEY` is set;
  free weekly ECR from nflverse otherwise.
- **Yahoo**: OAuth (auth URL → paste code → token exchange, tokens encrypted with Fernet
  at rest) and league/roster import into the existing `leagues` / `teams` / `rosters`
  tables, mapped through `players.yahoo_player_id`.
- **Sleeper**: trending adds/drops for the dashboard.

### Frontend (React)
Dark, data-dense UI in the style of Flock / FantasyPros, with light mode, responsive
layout, and accessible tables.
Pages: Dashboard (buy-low, sell-high, trending), Rankings, Advanced Stats, Trade
Calculator, Buy/Sell, Player page (weekly chart, advanced stats, signals), Ask AI
(chat that shows which tools were used), and Settings (league scoring, integrations).

## 5. Database changes (Alembic, idempotent for the existing DB)
- Fix migration `11919bb26697` (create `player_expected_points_weekly` if missing)
- New migration: `players.fantasypros_id`, unique index on `players.gsis_id`, indexes on
  `(player_id, season, week)`, new `player_snap_counts_weekly` and `expert_rankings` tables
- Rewrite `populate_stats.py` as `python -m database.populate_stats [--seasons ...]`: loads
  missing seasons and always refreshes the current one, uses bulk inserts, and builds the
  ID crosswalk first

## 6. Testing
- Backend: pytest unit tests (scoring, projections, valuation, trade, insights,
  leaderboards, tool validation, agent loop with a fake LLM, integration clients with
  mocked HTTP) plus API tests through FastAPI `TestClient` on a seeded DB. Target ≥80%
  coverage on new code.
- Frontend: Vitest + Testing Library for components and utilities; `tsc` and `vite build` must pass.
- End to end: run both servers against the real repopulated DB and exercise every page
  in the browser.

## 7. What needs you
- Add `FANTASYPROS_API_KEY=...` to `backend/.env` (never paste keys in chat).
- Add `TOKEN_ENCRYPTION_KEY=...` (the README gives the one-line command to generate it).
- Yahoo: sign in once in the browser to authorize. Your client ID and secret are already in `.env`.

## 8. Out of scope for this pass (follow-ups)
Dynasty values and rookie picks, injuries feed, bye-week-aware projections, multi-user
auth, Postgres, deployment, and the RB Vision Score / Big Data Bowl work (left untouched).
