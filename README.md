# Fantasy Football Toolkit

> "Your fantasy platform tells you what happened. This tells you what to do next."

A FantasyPros / Flock-style fantasy football toolkit built on free, open NFL data
(nflverse), with optional FantasyPros and Yahoo integrations.

**Core rule:** every number comes from real data and deterministic, tested code. The LLM
only *explains* numbers that came back from a tool call. Every output is labeled as one of:

| Label | Meaning | Source |
|---|---|---|
| **Actual** | What happened (box score, fantasy points) | nflverse weekly stats |
| **Expected** | What the player's usage was worth (targets, carries, air yards, red-zone looks) | nflverse `ff_opportunity` model |
| **Projection / trade value** | Rest-of-season outlook | This app's model (explained below) |
| **ECR** | Expert consensus ranking | FantasyPros (free weekly snapshot, or your API key) |

## Features

- **Data pipeline:** six seasons of weekly stats, Next Gen Stats, expected fantasy points,
  snap counts, cross-platform player IDs, and FantasyPros weekly ECR, loaded into SQLite by one command
- **Rankings:** rest-of-season rankings for your league's scoring (PPR, half, standard,
  TE premium, superflex) with actual, expected, and projected points side by side
- **Advanced-stat leaderboards:** 26 whitelisted metrics (RYOE, separation, YAC over
  expected, CPOE, time to throw, target share, EPA per play, and more), volume-weighted the right way
- **Player pages:** season summary, weekly game log, NGS advanced stats, projection
- **Trade calculator:** values both sides, applies a consolidation discount, gives a verdict, and suggests players that would balance the deal
- **Buy low / sell high:** finds players whose production is out of line with their usage, with plain-English reasons
- **Integrations:** Sleeper trending adds/drops, FantasyPros ROS rankings (with your key), and Yahoo league and roster import (OAuth)
- **AI assistant and agent tools:** 12 validated tools that any agent can call over REST
  or MCP (Claude Desktop / Claude Code), plus a built-in Groq-powered chat
- **Web app:** React UI with a dashboard, rankings, advanced stats, trade calculator, buy/sell market, player pages, AI chat, and settings; dark and light themes, mobile-friendly

## Quick start

Prerequisites: Python 3.11+ and Node.js 20+.

### 1. Backend setup

```bash
cd backend
python -m venv venv
venv\Scripts\activate            # Windows
# source venv/bin/activate       # macOS / Linux
pip install -r requirements.txt
copy .env.example .env           # macOS / Linux: cp .env.example .env
```

### 2. Database and data

```bash
python -m database.create_tables      # creates a new database, or upgrades an existing one to the latest schema
python -m database.populate_stats     # downloads 2021 through the current season (~1 minute)
```

Re-run `python -m database.populate_stats` weekly during the season. It reloads the current
season and fills in any missing ones. Other options:

```bash
python -m database.populate_stats --seasons 2025 2026   # reload specific seasons
python -m database.populate_stats --all                 # reload everything
```

A season only replaces existing rows after its download succeeds, so a network hiccup
never wipes data. `populate_stats` also brings the schema up to date first, and it works on
databases built by the project's older scripts (all migrations are idempotent).
`alembic upgrade head` still works for databases Alembic already tracks.

### 3. Run the API

```bash
uvicorn main:app --reload
```

Interactive docs: <http://localhost:8000/docs>. Try
<http://localhost:8000/api/rankings?position=RB&scoring=half&limit=10>.

### 4. Run the web app

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>. The UI talks to `http://localhost:8000` by default; set
`VITE_API_BASE` in `frontend/.env.local` to point it somewhere else.

## Using the app

| Page | What you can do |
|---|---|
| **Dashboard** | Top buy-low and sell-high players with the reason behind each, Sleeper's most-added players, data freshness |
| **Rankings** | Rest-of-season rankings by position; sort by actual, expected, or projected points, trade value, or ECR |
| **Advanced Stats** | Pick a category and metric, season, and minimum volume, then rank every qualified player |
| **Trade Calculator** | Search players into "You give" / "You get". See each side's value, the verdict meter, and suggestions that even it out; the URL is shareable |
| **Buy / Sell** | Full buy-low and sell-high lists with every reason, filtered by position |
| **Player page** | Weekly chart of actual vs expected points, game log, NGS stats, projection, signals |
| **Ask AI** | Ask questions in plain English; each answer shows which tools it used |
| **Settings** | League scoring, size, superflex, and TE premium (used everywhere); connect Yahoo; check integrations |

## How the numbers work

### Scoring
Fantasy points are computed from raw stats, so any scoring works. With standard / PPR
settings the formula reproduces nflverse's published `fantasy_points` and
`fantasy_points_ppr` exactly (checked on all 99,183 player-weeks in the database).
Expected points from nflverse are PPR-based and are converted to other scoring with
`expected - (1 - points_per_reception) × expected_receptions`.

### Season leaderboards
Next Gen Stats publish weekly rows plus a **week-0 row with official season totals**.
Weekly rows only exist for players who met a weekly minimum, so the season row is more
complete. Each player's season value comes from the official row when it exists, and
otherwise from a **volume-weighted** aggregate of weekly rows (Σ RYOE / Σ attempts, never
an average of weekly rates). Example: Derrick Henry 2025 shows NGS's official 1.05 RYOE/att on
307 attempts. The old code showed 1.11 because it averaged weekly rates and counted the
season row twice.

### Rest-of-season projection (model)
For each QB/RB/WR/TE this season:

1. `rate = 0.6 × actual PPG + 0.4 × expected PPG` (usage is stickier than production)
2. `ros_ppg = w × rate + (1 − w) × prior`, where `w = games / (games + 4)`. The prior is
   last season's PPG if the player had at least 4 games, otherwise the position's
   replacement level.

### Value over replacement and trade value
- **Replacement level** depends on your league: starters league-wide (teams × slots, with
  FLEX and superflex filled by the best remaining players) plus each position's share of bench spots.
  The next player down is the "waiver-wire" baseline.
- **`trade_value = max(0, ros_ppg − replacement_ppg) × weeks_remaining`**: rest-of-season
  fantasy points above what you'd get from waivers. Weeks remaining run through week 17.

### Trade calculator
Each side is sorted by value and weighted **1.0, 0.9, 0.8, …** (minimum 0.5). Lineup spots
are limited, so two mid-tier players are worth less than their raw sum. Both sides get the
same treatment, so an even 2-for-2 stays even. Verdict from the percent difference:
**≤ 5% fair, ≤ 15% slight edge, more is lopsided.** "Balance it" suggests players not in
the deal whose addition brings the trade closest to even.

### Buy low / sell high
Per player (3+ games):
- **FPOE** = actual − expected fantasy points per game
- **TD luck** = actual TDs − expected TDs
- **Usage trend** = expected points over the last 3 games vs the season
- **Recent form** = last-3 PPG vs season PPG (other managers react to this)

**Buy low:** a valuable role (expected PPG at or above replacement), FPOE ≤ −1, usage not
collapsing. **Sell high:** the mirror image. Only players with positive trade value qualify.
Scores are a percent of expected points, so QBs don't drown out other positions.

## API reference

All JSON. Errors use `{"detail": ...}` with a real status code (404, 422, 502, 503).
League parameters accepted by every valuation endpoint: `scoring=ppr|half|std`,
`teams=4-20`, `superflex=true|false`, `te_premium=0-1`.

| Method | Path | What it returns |
|---|---|---|
| GET | `/api/health` | `{"status": "ok"}` |
| GET | `/api/meta` | Current season, latest week, seasons loaded, integration status |
| GET | `/api/players/search?q=` | Name search (typo-tolerant), best match first |
| GET | `/api/players/{id}` | Profile: summary, projection, NGS stats, game log, buy/sell signals |
| GET | `/api/rankings?position=` | Rest-of-season rankings with trade value and weekly ECR |
| GET | `/api/leaderboards/metrics` | The metric catalog |
| GET | `/api/leaderboards?category=&metric=` | Advanced-stat leaderboard (`season`, `min_volume`, `position`, `ascending`, `limit`) |
| GET | `/api/experts/rankings` | FantasyPros ECR (ROS via API key if set, else the free weekly snapshot) |
| POST | `/api/trade/evaluate` | Body `{"give": [ids], "get": [ids], "settings": {...}}`; values, verdict, balance suggestions |
| GET | `/api/insights/buy-low` | Buy-low candidates with reasons |
| GET | `/api/insights/sell-high` | Sell-high candidates with reasons |
| GET | `/api/insights/trending?type=add\|drop` | Sleeper trending players |
| GET | `/api/integrations/status` | Which integrations are configured and connected |
| GET | `/api/integrations/yahoo/auth-url` | Yahoo sign-in URL |
| POST | `/api/integrations/yahoo/exchange` | Body `{"code": "<code or full redirect URL>"}` |
| GET | `/api/integrations/yahoo/leagues` | Your Yahoo NFL leagues |
| POST | `/api/integrations/yahoo/leagues/{key}/import` | Import teams and rosters |
| GET | `/api/agent/tools` | Tool definitions (JSON Schema) |
| POST | `/api/agent/tools/{name}` | Body `{"arguments": {...}}`; run one tool |
| POST | `/api/agent/chat` | Body `{"messages": [{"role": "user", "content": "..."}]}`; answer plus tool trace |
| GET | `/api/rankings/rushing` | Original rushing endpoint (kept for compatibility; now uses the fixed leaderboard math) |

Full TypeScript types for every response: [`docs/API.md`](docs/API.md).

## AI assistant and agent tools

The same 12 tools are exposed three ways: `search_players`, `get_player_profile`,
`get_player_game_log`, `compare_players`, `get_fantasy_rankings`,
`list_leaderboard_metrics`, `get_leaderboard`, `evaluate_trade`, `get_buy_low_candidates`,
`get_sell_high_candidates`, `get_expert_rankings`, and `get_trending_players`.
Arguments are validated against each tool's JSON Schema, and players can be passed by name
(typo-tolerant; ambiguous names like "Mike Williams" return the candidates) or by id.
If a tool fails unexpectedly, the error is logged on the server and the model only sees a
generic message.

1. **REST:** `GET /api/agent/tools` lists them, `POST /api/agent/tools/{name}` runs one.
2. **Built-in chat:** `POST /api/agent/chat` runs a multi-round tool-calling loop on Groq
   (set `GROQ_API_KEY`). Try it from the command line:
   `python -m testing.groq_test "Is trading Bijan Robinson for Ja'Marr Chase fair?"`
3. **MCP server** for Claude Desktop, Claude Code, or any MCP client:
   ```bash
   pip install -r requirements-mcp.txt
   python mcp_server.py               # stdio transport
   ```
   Claude Desktop config (`claude_desktop_config.json`):
   ```json
   {
     "mcpServers": {
       "fantasy-football": {
         "command": "C:\\path\\to\\Fantasy-Football-App\\backend\\venv\\Scripts\\python.exe",
         "args": ["mcp_server.py"],
         "cwd": "C:\\path\\to\\Fantasy-Football-App\\backend"
       }
     }
   }
   ```
   Claude Code: `claude mcp add fantasy-football -- <venv python> <path>/backend/mcp_server.py`

Snap counts come from Pro Football Reference, whose terms restrict using its content to
prompt AI models, so they're withheld from tool results unless `AGENT_SHARE_PFR_DATA=true`.

## Integrations

| Integration | Needs | What it adds |
|---|---|---|
| Sleeper | nothing | Trending adds/drops (cached 15 min) |
| FantasyPros API | `FANTASYPROS_API_KEY` | ROS consensus rankings. Without a key you still get the free weekly ECR snapshot |
| Yahoo Fantasy | `YAHOO_CLIENT_ID`, `YAHOO_CLIENT_SECRET`, `YAHOO_REDIRECT_URI`, `TOKEN_ENCRYPTION_KEY` | Import your leagues, teams, and rosters |

**FantasyPros limits:** their terms allow 1 call/second and 100 calls/day for personal,
non-commercial use. The app stores only the latest ranking snapshot and refreshes it at
most every 6 hours. Every call is logged in the database, so the daily cap survives
restarts, and after a failed call it waits 30 minutes before trying again. Attribution is required.

### Connecting Yahoo
1. Generate an encryption key for stored tokens and add it to `backend/.env` as `TOKEN_ENCRYPTION_KEY`:
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
2. Your Yahoo app's redirect URI must match `YAHOO_REDIRECT_URI` exactly (Yahoo requires
   HTTPS, e.g. `https://localhost:8000/callback`).
3. Get the sign-in link from `GET /api/integrations/yahoo/auth-url` or **Settings → Connect Yahoo**
   and approve access. Yahoo redirects to your HTTPS redirect URI, which the dev server
   can't serve, so the browser shows an error page. **That's expected.** Copy the whole URL from the address bar.
4. Paste it into `POST /api/integrations/yahoo/exchange` or the Settings page. The app
   pulls out the `code`, checks the one-time `state`, and stores encrypted tokens. Tokens refresh automatically.
5. List your leagues and import one. Rosters are matched to players through the nflverse ID
   crosswalk, and anything unmatched (kickers, defenses) is listed in the response.

Yahoo requires showing "Fantasy data provided by Yahoo Fantasy" wherever its data appears.

## Configuration

Settings come from environment variables or `backend/.env` (never commit it; see `backend/.env.example`).

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///backend/database/fantasy.db` | Any SQLAlchemy URL |
| `CORS_ORIGINS` | `["http://localhost:5173","http://127.0.0.1:5173"]` | JSON list of allowed web origins |
| `FANTASY_END_WEEK` | `17` | Last fantasy week (drives weeks remaining) |
| `ALLOWED_HOSTS` | `["localhost","127.0.0.1"]` | Host headers the API accepts (blocks DNS rebinding) |
| `FANTASYPROS_API_KEY` | — | Enables FantasyPros ROS rankings |
| `YAHOO_CLIENT_ID` / `YAHOO_CLIENT_SECRET` | — | Yahoo OAuth app credentials |
| `YAHOO_REDIRECT_URI` | `oob` | Must match the Yahoo app setting |
| `TOKEN_ENCRYPTION_KEY` | — | Fernet key used to encrypt OAuth tokens at rest |
| `GROQ_API_KEY` | — | Enables the AI chat |
| `GROQ_MODEL` | `openai/gpt-oss-20b` | Groq model for the chat |
| `AGENT_SHARE_PFR_DATA` | `false` | Include PFR-sourced snap counts in AI tool results |

## Data sources

All the core data is free and needs no key, via [`nflreadpy`](https://github.com/nflverse/nflreadpy).
[`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) covers every source the app uses or could use:
what it has, how to access it, license terms, update schedule, and which ones to avoid
(KeepTradeCut, FantasyCalc for calculators, Sports-Reference scraping).

## Testing

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest                                   # all tests run on an in-memory database
python -m pytest --cov=. --cov-report=term-missing # with coverage
```

GitHub Actions ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs the suite on every pull request.
Tests never touch `database/fantasy.db` or read `backend/.env`. All HTTP calls to
Sleeper, FantasyPros, Yahoo, and Groq are mocked.

```bash
cd frontend
npm test          # Vitest + Testing Library
npm run build     # type-check and production build
```

## Project structure

```
backend/
  config.py                 settings from env / .env
  main.py                   FastAPI app (uvicorn main:app)
  api/                      thin routers: data, market, integrations, agent, legacy
  services/                 tested business logic (scoring, projections, valuation, leaderboards, players, trade, insights, agent)
  integrations/             sleeper, fantasypros, yahoo, token encryption
  tools/                    agent tool registry and definitions
  mcp_server.py             MCP server over the same tools
  database/                 models, migrations helpers, data loader
  alembic/                  schema migrations
  tests/                    pytest suite
frontend/                   React + TypeScript + Vite web app
docs/                       plan, data sources, API contract
```

## Design notes

- **Player identity crosswalk:** Yahoo, Sleeper, FantasyPros, PFR, and nflverse each use
  different IDs. `players` is the hub, keyed by nflverse's `gsis_id` (unique) and filled
  from the DynastyProcess crosswalk.
- **Foreign keys are enforced** on every SQLite connection.
- **Local-only API.** It has no login, so it accepts only `localhost` / `127.0.0.1` Host
  headers and rejects cross-site POSTs (a browser `Origin` not in `CORS_ORIGINS`). curl,
  scripts, and the MCP server send no `Origin` and work normally. Keep uvicorn on 127.0.0.1.
- **Cached valuations** are keyed by league settings plus a stamp written by every
  `populate_stats` run, so reloads (stat corrections included) show up immediately.
- **SQLite is a deliberate, temporary choice.** It's zero-config for solo use; moving to
  Postgres is a `DATABASE_URL` change.
- **OAuth tokens are encrypted at rest** (Fernet). The app is single-user for now, so a
  connection belongs to one local user row.

## Roadmap

See [`docs/PLAN.md`](docs/PLAN.md) for the full plan and what's out of scope for now: dynasty values,
injury-aware projections, bye weeks, multi-user auth, deployment.
Research work: [`FutureWork.md`](FutureWork.md) (RB Vision Score) and [`DATA_AUDIT.md`](DATA_AUDIT.md) (Big Data Bowl audit).
