# Data sources

Researched 2026-10-22. "In use" means the app loads it today.

## Free, no key

| Source | What it gives | Access | Terms | Freshness | Status |
|---|---|---|---|---|---|
| nflverse player stats | Weekly box score, EPA, CPOE, target share, air-yards share, WOPR, fantasy points | `nflreadpy.load_player_stats` | Repo CC-BY-4.0; the underlying NFL data belongs to its owners | Nightly after games | **In use** |
| nflverse Next Gen Stats | Passing (time to throw, CPOE, aggressiveness), rushing (RYOE, efficiency, 8+ box rate), receiving (separation, cushion, YAC over expected) | `load_nextgen_stats(stat_type=…)` | NFL-owned data | Nightly | **In use** |
| ffopportunity (expected fantasy points) | Expected points, yards, TDs per player-week from usage; drives buy-low/sell-high | `load_ff_opportunity` | GPL-3.0 code | Weekly in season | **In use** |
| nflverse snap counts | Offensive snap counts and percentages | `load_snap_counts` | Sourced from Pro Football Reference (see caution below) | 4×/day | **In use** (kept out of AI prompts by default) |
| DynastyProcess ID crosswalk | gsis ↔ Yahoo ↔ Sleeper ↔ FantasyPros ↔ PFR ↔ ESPN ids | `load_ff_playerids` | GPL-3.0 | Weekly | **In use** |
| FantasyPros ECR (via DynastyProcess) | Weekly and redraft expert consensus rankings | `load_ff_rankings(type="week")` | GPL-3.0 repo | Weekly | **In use** |
| Sleeper API | Trending adds/drops, player database | `https://api.sleeper.app/v1/players/nfl/trending/{add\|drop}` | Non-commercial, under 1,000 calls/min, attribution requested | Live | **In use** |
| nflverse play-by-play | EPA, WPA, CPOE, air yards, xYAC, pass rate over expected | `load_pbp` | CC-BY-4.0 repo | Nightly | Available |
| PFR advanced stats | Broken tackles, drops, yards before/after contact, pressures | `load_pfr_advstats` | Sports-Reference terms (see caution) | 4×/day | Available |
| FTN charting | Play action, motion, RPO, screens, catchable/contested balls, blitzers, box count | `load_ftn_charting` (2022+) | **CC-BY-SA 4.0**; must credit "FTN Data via nflverse" | ~48h after games | Available |
| nflverse participation | Personnel, routes, coverage type, pressure | `load_participation` | CC-BY-SA (FTN, 2023+) | **After the postseason only** | Available |
| Injuries / depth charts / schedules (with Vegas lines) | Weekly injury status, depth order, spreads and totals | `load_injuries`, `load_depth_charts`, `load_schedules` | nflverse | Daily / every 5 min | Available |
| Open-Meteo | Game-day weather | REST, no key | Non-commercial, CC-BY 4.0, 10k calls/day | Live | Available |
| NFL Big Data Bowl | Player-tracking data | Kaggle download | CC BY-NC 4.0, no redistribution | Historical only | Used by the research scripts only |

## Keyed

| Source | What it gives | Notes |
|---|---|---|
| FantasyPros API v2 (`FANTASYPROS_API_KEY`) | Consensus rankings (ROS, draft, waiver), projections, news, injuries | `x-api-key` header. Personal non-commercial use, **1 call/s and 100 calls/day**, attribution required, no storing historical player stats, non-compete clause. The app keeps only the latest ECR snapshot and refreshes it at most every 6 hours. |
| Yahoo Fantasy Sports API (`YAHOO_CLIENT_ID/SECRET`) | Your leagues, settings, teams, rosters, free agents, transactions | OAuth 2.0; access tokens last 1 hour; refresh tokens stored encrypted. Yahoo now requires an approved application and showing "Fantasy data provided by Yahoo Fantasy". |
| Groq (`GROQ_API_KEY`) | LLM for the AI assistant | Only receives the user's question and tool results. |

## Use with caution or avoid

- **Sports-Reference / Pro Football Reference:** blocks clients making more than 20 requests per minute and forbids scraping tools. Its data-use page also bans using its content for "training, fine-tuning, prompting, or instructing" AI models. nflverse's snap counts and `pfr_advstats` come from PFR, so the app keeps snap data out of AI tool results unless `AGENT_SHARE_PFR_DATA=true`.
- **KeepTradeCut:** its terms prohibit scraping and using its data on other sites. Not used.
- **FantasyCalc:** a free values API, but its terms forbid using it to build a substitute trade calculator. Not used; the app's trade values come from its own model.
- **ESPN unofficial endpoints:** undocumented, and the Disney terms of use prohibit automated data collection. Not used.
- **MySportsFeeds:** paid ($5+/month for NFL).
