# Fantasy Football AI Assistant

> "Your fantasy platform tells you what happened. This tells you what to do next."

An AI-powered assistant that connects to your existing fantasy football league (Yahoo, Sleeper, ESPN) and gives personalized, explained recommendations — start/sit calls, waiver targets, trade suggestions — grounded in real player and league data, not guesses.

This is an early-stage personal project, currently in **Phase 1 (MVP foundation)**.

## Core idea

Recommendations flow through a pipeline, not a single LLM call:

```
Fantasy/NFL data → data processing → statistical analysis → recommendation → AI explanation
```

The LLM's job is to **explain and personalize** a recommendation already backed by real numbers — never to invent a stat or guess a projection on its own.

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend | FastAPI (Python) | Async support for calling external APIs without blocking |
| Frontend | React (Vite) | Fast dev server, standard React setup |
| Database | SQLite + SQLAlchemy ORM | Zero-config for solo development; ORM makes a future Postgres migration a connection-string change, not a rewrite |
| NFL stats data | [`nflreadpy`](https://github.com/nflverse/nflreadpy) | Free, open, no API key — reads published nflverse data (weekly stats, rosters, etc.) |
| League data | Yahoo Fantasy Sports API | Read-only OAuth access (pending Yahoo's app approval) |

## Project structure

```
fantasy-football-app/
├── frontend/               # React + Vite app
├── backend/
│   ├── main.py             # FastAPI app entrypoint
│   ├── database/
│   │   ├── models.py       # SQLAlchemy table definitions
│   │   ├── create_tables.py
│   │   ├── populate_stats.py   # pulls nflreadpy data into the DB
│   │   └── fantasy.db      # not committed — generated locally
│   └── .env                # not committed — secrets/config
└── .gitignore
```

## Database schema (current)

- **users** — app accounts
- **platform_connections** — stored OAuth tokens per user, per platform (Yahoo, Sleeper, etc.)
- **leagues** — a user's imported league(s), including scoring settings
- **teams** — teams within a league
- **players** — a platform-agnostic player identity hub, with crosswalk IDs (`gsis_id`, `yahoo_player_id`, `sleeper_player_id`) so the same real player can be matched across data sources
- **rosters** — weekly roster snapshots per team
- **player_stats_weekly** — standard box-score stats (yards, TDs by type, receptions, targets)
- **player_advanced_stats_weekly** — advanced/Next Gen-style metrics (separation, time to throw, etc.) — *not yet populated*
- **recommendations** — generated recommendations, including a stored data snapshot so every recommendation can be explained after the fact

Foreign keys are enforced at the connection level (SQLite has them off by default).

## Getting started

### Prerequisites
- Python 3.11+
- Node.js (for the frontend)

### Backend setup

```bash
cd backend
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Mac/Linux
pip install fastapi uvicorn sqlalchemy nflreadpy python-dotenv httpx
```

Create a `.env` file in `backend/` with:

```
YAHOO_CLIENT_ID=your_client_id
YAHOO_CLIENT_SECRET=your_client_secret
YAHOO_REDIRECT_URI=http://localhost:8000/callback
```

Create the database:

```bash
cd database
python create_tables.py
```

Populate it with real NFL stats:

```bash
python populate_stats.py
```

Run the API:

```bash
cd ..
uvicorn main:app --reload
```

### Frontend setup

```bash
cd frontend
npm install
npm run dev
```

## Current status

- [x] Database schema designed and implemented (SQLAlchemy + SQLite)
- [x] Weekly player stats populated from `nflreadpy`
- [x] FastAPI + React talking to each other (basic health check endpoint)
- [ ] Yahoo OAuth flow (pending Yahoo API access approval)
- [ ] Advanced/Next Gen stats populated
- [ ] Start/sit recommendation logic (Phase 2)

## Design notes worth knowing

- **Player identity crosswalk**: Yahoo, Sleeper, and nflverse each use different IDs for the same player. The `players` table is the hub every other table joins through, using `gsis_id` as the anchor from nflverse data.
- **OAuth tokens are currently stored as plain text.** This is acceptable for solo local development only — before any real user other than the developer connects an account, tokens need encryption at rest.
- **SQLite is a deliberate, temporary choice**, not a permanent one. It's zero-config for solo iteration, but only supports one writer at a time and typically doesn't persist on ephemeral hosting disks. The plan is to migrate to Postgres once there's more than one real concurrent user or a real deploy target.