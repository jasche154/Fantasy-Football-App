# Data sources

Researched 2026-10-22; weekly sources added 2026-10-23. "In use" means the app loads it today.

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
| nflverse schedules (with Vegas lines) | Games, scores, closing spreads and totals (implied team totals) | `load_schedules` | nflverse | Every 5 min in season | **In use** (matchups, strength of schedule) |
| Injuries / depth charts | Weekly injury status, depth order | `load_injuries`, `load_depth_charts` | nflverse | Daily | Available |
| Open-Meteo | Game-day weather | REST, no key | Non-commercial, CC-BY 4.0, 10k calls/day | Live | Available |
| NFL Big Data Bowl | Player-tracking data | Kaggle download | CC BY-NC 4.0, no redistribution | Historical only | Used by the research scripts only |

## Keyed

| Source | What it gives | Notes |
|---|---|---|
| FantasyPros API v2 (`FANTASYPROS_API_KEY`) | Consensus rankings (ROS, draft, waiver), projections, news, injuries | `x-api-key` header. Personal non-commercial use, **1 call/s and 100 calls/day**, attribution required, no storing historical player stats, non-compete clause. The app keeps only the latest ECR snapshot and refreshes it at most every 6 hours. |
| Yahoo Fantasy Sports API (`YAHOO_CLIENT_ID/SECRET`) | Your leagues, settings, teams, rosters, free agents, transactions | OAuth 2.0; access tokens last 1 hour; refresh tokens stored encrypted. Yahoo now requires an approved application and showing "Fantasy data provided by Yahoo Fantasy". |
| The Odds API (`ODDS_API_KEY`) | Vegas player props: pass/rush/receiving yards, receptions, pass TDs, INTs, anytime TD, from US books | **In use** for Week X rankings. Free plan: 500 credits/month; `/events` is free, each game's props cost markets × regions (7). Each game is bought once, within 30 h of kickoff (~112 credits/week); purchases are logged per event, and refreshes stop when fewer than 17 credits remain. |
| Groq (`GROQ_API_KEY`) | LLM for the AI assistant | Only receives the user's question and tool results. |

## Unofficial, personal use (in use, can be turned off)

| Source | What it gives | Access | Caveats |
|---|---|---|---|
| Yahoo Sports expert rankings | Weekly positional ranks from 40+ rankers, including Josh Norris (id 1408) and Hayden Winks (7666), plus consensus | `https://sports.yahoo.com/api/fanPro/?position=RB&type=WK&wtype=WK&week=7&scoring=PPR&experts=show&year=2026` (the JSON behind Yahoo's rankings articles; `position=ALL` fails for weekly) | Undocumented; Yahoo's terms restrict automated access. 12 requests per refresh, cached 3 h, latest snapshot only. Players map through the Yahoo id crosswalk. |
| ESPN fantasy projections | Weekly projected stat lines (ESPN stat ids 3/4/20 passing, 24/25 rushing, 53/42/43 receiving) | `lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leaguedefaults/3?view=kona_player_info` with an `X-Fantasy-Filter` header | Undocumented; the Disney terms prohibit automated data collection. 1 request per refresh, cached 3 h. |

Remove a source from `WEEKLY_SOURCES` to stop contacting it.

## Use with caution or avoid

- **Sports-Reference / Pro Football Reference:** blocks clients making more than 20 requests per minute and forbids scraping tools. Its data-use page also bans using its content for "training, fine-tuning, prompting, or instructing" AI models. nflverse's snap counts and `pfr_advstats` come from PFR, so the app keeps snap data out of AI tool results unless `AGENT_SHARE_PFR_DATA=true`.
- **KeepTradeCut:** its terms prohibit scraping and using its data on other sites. Not used.
- **FantasyCalc:** a free values API, but its terms forbid using it to build a substitute trade calculator. Not used; the app's trade values come from its own model.
- **MySportsFeeds:** paid ($5+/month for NFL).
