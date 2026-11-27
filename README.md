# Fantasy Football Toolkit

> "Your fantasy platform tells you what happened. This tells you what to do next."

A FantasyPros / Flock-style fantasy football toolkit built on free, open NFL data
(nflverse), with weekly rankings that blend Vegas player props, Yahoo's Josh Norris and
Hayden Winks, Yahoo's expert consensus, ESPN, and an opponent-adjusted matchup model.

**Core rule:** every number comes from real data and deterministic, tested code. The LLM
only *explains* numbers that came back from a tool call. Every output is labeled as one of:

| Label | Meaning | Source |
|---|---|---|
| **Actual** | What happened (box score, fantasy points) | nflverse weekly stats |
| **Expected** | What the player's usage was worth (targets, carries, air yards, red-zone looks) | nflverse `ff_opportunity` model |
| **Projection / trade value** | Rest-of-season outlook | This app's model (explained below) |
| **ECR** | Expert consensus ranking | FantasyPros (free weekly snapshot, or your API key) |
| **Week X projection** | This week's projected points, with every source's number shown | Blend of Vegas props, Yahoo experts, ESPN, and this app's matchup model |

## Features

- **Data pipeline:** six seasons of weekly stats, Next Gen Stats, expected fantasy points,
  snap counts, cross-platform player IDs, and FantasyPros weekly ECR, loaded into SQLite by one command
- **Week X rankings:** projected points for the current (or any) week, blended from Vegas
  player props (highest weight), Yahoo's Josh Norris & Hayden Winks, Yahoo's 40+ expert
  consensus, ESPN, and a matchup model, with each source's number side by side. Click any
  column to sort by it. The nav shows the real week (e.g. "Week 8 Rankings"). The first build
  of a week is saved to the local database, so later loads (even after a restart) are instant
- **Injury-aware projections:**
  - Sleeper's live injury tags and the official practice reports become a learned chance to
    play.
  - When a player may sit, his work is passed to teammates, using what happened in games he
    missed this season. Example: *Braelon Allen +7.5 points if Breece Hall sits*.
  - Backtests on 2025 and 2024 show the injury layer cuts weekly error by about 10%.
- **Explanations on request:** a **Why?** button on every projection lists each step from
  recent production to the final number.
- **Floor/ceiling ranges** measured from 2025 outcomes by position and projection tier, in games
  played in a normal role, plus **breakout and regression flags**:
  - growing snap share (the free stand-in for route share) and efficient part-timers;
  - starters returning from injury, with how much their fill-in stands to lose.
- **Projection history:** every pre-kickoff projection is stored locally with each source's
  number, and `python -m services.projection_eval` scores them against what happened
- **Start/Sit comparer, Waiver Wire assistant** (with FAAB bids), and **Trade finder**
- **Adjusted strength of schedule:** fantasy points each defense allows by position,
  corrected for the offenses it faced; drives weekly matchups and rest-of-season values
- **Rankings:** rest-of-season rankings for your league's scoring (PPR, half, standard,
  TE premium, superflex) with actual, expected, and projected points side by side, plus
  rest-of-season and **playoff-weeks strength of schedule** (your league's playoff weeks)
- **Chart Studio:** charts built automatically from your selection (two metrics → scatter split at
  the medians, one → ranked bars, players → week-by-week trend), five one-click presets, SVG download.
  Hover a point for the player's name; click a scatter point for a card with both values, ranks and quadrant
- **Advanced-stat leaderboards:** 26 whitelisted metrics (RYOE, separation, YAC over
  expected, CPOE, time to throw, target share, EPA per play, and more), volume-weighted the right way
- **Player pages:** season summary, weekly game log, NGS advanced stats, projection, position
  percentiles for key stats, and "what changed" (last 3 games vs the rest of the season)
- **Odds page:** this week's closing spreads, totals and implied team totals, plus the player
  props already bought, per game. It never buys odds itself
- **Trade calculator:** values both sides, applies a consolidation discount, gives a verdict, and suggests players that would balance the deal
- **Buy low / sell high:** finds players whose production is out of line with their usage, with plain-English reasons
- **Matchup page:** your Yahoo opponent this week, both lineups, and your win probability from
  20,000 simulated weeks (next to Yahoo's own). It also recommends the expected, **upside** or
  **safe** lineup, whichever wins most often against this opponent. At the bottom: the
  recommended lineup's projection and win chance, and your bench with each player's projection.
- **Playoff outlook and planner:** your roster across your league's playoff weeks (read from
  Yahoo; weeks 16–18 by default): opponent, bye, projection per week, the best lineup each
  week, your weakest week, and the free agents and trade targets who beat your weakest starter
  at each position over those weeks.
- **League power rankings:** every team's lineup strength, record, and playoff odds from
  10,000 simulations of Yahoo's remaining schedule, each team's all-play record and luck, each
  manager's habits (active trader, waiver regular, quiet; the positions they trade for and pick
  up) and tags with their evidence (FAAB whale, waiver hawk, RB hunter, trade shark...), and the
  season's past trades graded on the points scored since.
- **Player news** from Sleeper on player pages and under your lineup.
- **Compare players:** any 2–4 players game by game across up to four seasons, any stat,
  with a 3-game average and a per-season summary.
- **Kicker and defense projections** from advanced team stats: field-goal tries from the
  implied total and red-zone TD / stop rates, the kicker's accuracy by distance with
  wind and cold, and log5 sacks and takeaways with points-allowed tiers. Shown in Week X
  and used in My Team lineups with your league's own K/DEF scoring.
- **Model accuracy view:** live projection error, floor/ceiling coverage, the held-out
  backtest and the learned source weights.
- **My Team (Yahoo):** import your Yahoo leagues, with their own scoring and roster slots, and get:
  - your roster with this week's projections and a best-lineup check ("Start Gibbs over Corum, +10.5");
  - waivers limited to players actually free in your league;
  - trade ideas with the real teams in your league, matched to each team's weak positions.
  The AI assistant uses the same roster.
- **Replay League:** a single-player season replay with its own page. Pick a past season (2018 on, or
  "random"), draft against CPU managers who only know that summer's ADP, then play it week by week with
  Wednesday waivers, FAAB bidding or free agency only, and trades; matchups are scored with the players' real points. Every number shown
  before a week is played is point-in-time (preseason ADP, that week's pre-game ESPN/Sleeper projection).
  Players carry Yahoo-style injury tags (Q, D, O, IR, PUP, SUS, EXE = commissioner's exempt list) from that week's official report and roster
  status; a tagged starter raises a lineup warning. Each team has one IR slot: a player tagged O, IR or PUP
  can be moved there, which frees his roster spot, and can trade places with another injured player. When
  he loses the tag you must activate him (dropping someone if the roster is full) or drop him before you
  can add, claim or trade for anyone. The Players screen shows each player's recent usage (snap %, targets,
  target share, carries). Two simulated seasons can be played too: the season in progress (real weeks so
  far, the rest simulated, with a full-season or "jump in now" draft) and next season, simulated whole
  (see [Simulated seasons](#simulated-seasons-replay-league)).
- **Integrations:** Sleeper trending adds/drops, FantasyPros ROS rankings (with your key), and Yahoo league and roster import (OAuth)
- **AI assistant and agent tools:** 17 validated tools that any agent can call over REST
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

**While the API is running, this happens on its own every week** (`services/auto_update.py`,
checked every 6 hours):
- A day after a week's last game, it loads that week's stats, which also updates the learned
  source weights.
- While the current week still has games left, it refreshes the outside sources and saves
  everyone's pre-game projection.

Set `AUTO_UPDATE=false` in `.env` to turn it off. You can still re-run
`python -m database.populate_stats` by hand any time; it reloads the current
season and fills in any missing ones. Other options:

```bash
python -m database.populate_stats --seasons 2025 2026   # reload specific seasons
python -m database.populate_stats --all                 # reload everything
```

`populate_stats` also loads:
- the schedule with closing Vegas spreads and totals;
- every season's official injury reports;
- each team's latest depth chart.

The Week X rankings, injury model and strength of schedule all use these.

To pull this week's outside rankings (Yahoo experts, ESPN, and Vegas props if
`ODDS_API_KEY` is set) without opening the web app:

```bash
python -m services.weekly_refresh                # current week, only sources older than their cache
python -m services.weekly_refresh --week 8 --force # re-fetch Yahoo and ESPN now
python -m services.weekly_refresh --force-props  # re-buy Vegas props already bought (costs credits)
```

Opening the Week X page refreshes stale sources for the current week too (other weeks just
show what's stored, and only one refresh runs at a time).

Replay League needs a past season's point-in-time data, imported once per season (about a minute each;
free public sources, no keys):

```bash
python -m services.replay_data --seasons 2019,2023   # or 2018-2025; --force re-imports
```

The Replay League page does the same from the browser: a season that is not ready has a
**Prepare &lt;season&gt; data** button under the new-league form. The import also loads the season's
injury reports and weekly roster status (nflverse). A season prepared before that existed is topped up
by running the same command again (without `--force`), or with the **Add &lt;season&gt; injury reports**
button. The same goes for snap counts (the "snaps" step): a season prepared before it existed gets them on
its next Prepare. The season in progress is prepared the same way (`--seasons 2026`), again each time a week
finishes, and once more when it is over (see [Simulated seasons](#simulated-seasons-replay-league)).

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
| **Matchup** | This week's Yahoo opponent. Both starting lineups with projections and ranges, live points as games finish, and your win probability (with Yahoo's for comparison). **Lineup strategy** compares three versions of your lineup: most points, upside (favoring ceilings) and safe (favoring floors). Each is simulated against the opponent, and the one that wins most often is recommended, with the swaps to make. **Updated lineup** at the bottom shows that lineup's projected total and win chance, then your **bench** with each player's projection |
| **My Team** | Your imported Yahoo team (pick the league if you have several). **Lineup:** starters and bench with this week's projection, range, flags and rest-of-season value; the best lineup under your league's slots, with "Start X over Y (+points)" advice and Start/Sit tags. **Waivers:** players on no roster in your league. **Trades:** fair offers with each real team, built from their roster, teams that need what you'd send first. **Playoffs:** each player's projection for every playoff week (your league's weeks from Yahoo, else 16–18), byes marked, the best lineup per week, your weakest week, and the **Playoff planner**: free agents and players on other teams who beat your weakest starter at each position by 3+ playoff points. **League:** power rankings by lineup strength, record, points for, remaining opponents, simulated playoff odds and projected wins, luck and all-play record, each manager's tendencies (also shown next to each team's trade offers) and tags with their evidence, and **Past trades** graded on the points scored since. **Player news** works like Yahoo's: a news icon next to each player with news, highlighted (with a dot) when there's news from the last 48 hours you haven't opened. Click it for a side panel with his latest notes (headline, summary, analysis, source link); opening it marks the news read (remembered in this browser). The **News feed** below the lineup lists the newest notes across your roster, new ones tagged, and clicking a name opens his panel. **Sync from Yahoo** re-imports now (the app also re-syncs every 6 hours) |
| **Week X Rankings** | This week's projections by position; pick any week. Each row shows the matchup grade (opponent-adjusted, #1 = easiest), the Vegas implied team total, the blended projection, and every source's number (props, Josh & Hayden, Yahoo, ESPN, model). For finished games, **Actual** shows what each player scored (green beat the projection by 20%+, red missed by 20%+, DNP sat). Click a column header (Proj, Actual, Team total, or any source) to sort by it. **Schedule strength** switches to defenses ranked by adjusted points allowed and each team's upcoming opponents. **Refresh sources** re-pulls Yahoo and ESPN (Vegas props follow their credit budget) |
| **Rankings** | Rest-of-season rankings by position; sort by actual, expected, or projected points, schedule strength, **Playoff SOS** (your league's playoff weeks, else the last three), trade value, or ECR |
| **Week X → Start / Sit** | Add up to four players. Each shows its projection, floor–ceiling range, injury status, "if <teammate> sits" scenarios, flags, and a **Why?** breakdown. A tiebreaker (most points / safe / upside) decides close calls |
| **Waiver Wire** | Players rostered in under 10–75% of ESPN leagues, ranked by rest-of-season value, this week's projection, next three matchups, and injury-driven opportunity, with a suggested FAAB bid |
| **Trade Calculator → Trade finder** | Put the players you'd move in "You give", pick a position you want, and get 1-for-1 and 2-for-1 offers that are fair by market value (expert consensus, how the other manager will judge it) with any edge from this app's values, best expected edge first, each with a rough chance they accept; results clear when you change the trade; **Load in calculator** opens one |
| **Chart Studio** | Click a quick chart (Volume pays, Separators, RYOE leaders, Downfield QBs, Opportunity score) or pick one or two season metrics and a position; the chart type is chosen for you. Add players to highlight them, or add players with no metric for a weekly trend of any stat. Hover any point for a card with the player, team and both values (guide lines run to each axis); click a scatter point for that player's values, ranks among the plotted players, and quadrant. **Download SVG** saves the chart |
| **Compare Players** | Add up to four players and pick seasons (up to four) and a stat: fantasy points, expected points, targets, carries, scrimmage yards, touchdowns or snap %. One chart puts every game on a single timeline ('25 W5 … '26 W7), with an optional 3-game average; the table summarizes each season (games, PPG, expected PPG, best, worst, usage). Links like `#/compare?ids=1,2&seasons=2025,2026` reopen a comparison |
| **Advanced Stats** | Pick a category and metric, season, and minimum volume, then rank every qualified player |
| **Trade Calculator** | Search players into "You give" / "You get". See each side's value with why (rank, rate, games left, chance to play, schedule), the verdict meter, the market's view, and suggestions that even it out. **Score on** an imported league to use its settings and rosters and see both starting lineups' gains; the URL (including the league) is shareable |
| **Buy / Sell** | Full buy-low and sell-high lists with every reason, filtered by position |
| **Player page** | Weekly chart of actual vs expected points, game log, NGS stats, projection, signals. **Position percentiles** rank his season stats against his position (green = good, red = bad, grey for context stats like aDOT; "Small sample" until he has enough volume). **What changed** compares his last 3 games with the earlier games (snap share, target share, air-yard share, targets, carries, pass attempts) and flags big moves |
| **Odds & Props** | Pick a week: every game's kickoff, closing spread and total, and each team's implied total. **Show props** opens the player props stored for that game (expected passing/rushing/receiving yards, receptions, TDs implied by the lines) with how long before kickoff they were bought. Props appear game by game on game day; before that the page says why they're missing |
| **Ask AI** | Ask questions in plain English; each answer shows which tools it used |
| **Replay League** | A game: replay a past season against CPU managers. **Start:** under **New league** pick what to play (a past season, or a simulated one: the live season with **Full season** or **Jump in now**, or next season), then a season (or Random), 4–16 teams, the **league format** (Standard, Superflex, Double flex, each with a short note, or **Custom**, which shows 1–10 bench spots (6 by default) and 0–3 starters per slot: QB, RB, WR, TE, FLEX, SFLX (superflex: a QB, RB, WR or TE), K, DST; at least one QB or SFLX, at most 12 starters; in superflex the CPUs value and draft quarterbacks much earlier), the **scoring** (PPR, half or standard, or **Custom**, which keeps the last preset as the base and shows points per reception 0–1.5, a TE premium of +0.5 or +1 per catch, 4 or 6 points per passing TD and −1 or −2 per turnover; played games always score exactly, but with custom settings the pre-game projections are estimates converted from the sites' PPR and standard numbers), your draft slot, the playoff weeks (14–16, 15–17 by default, or 16–18 when the season has a week 18) and how many teams make the playoffs (4–8, default 4 under 10 teams, 6 under 14, else 8), waivers (Wednesday waivers; FAAB bidding: $100 per team, blind bids, the highest bid wins, ties go by waiver priority, winning bids show in the activity log; or free agency only) and difficulty, then **Create league and draft**. Saved leagues are listed above the form (Resume, Delete). **Draft:** the board lists who is left by **Rank** (the preseason expert rank, or the ADP order for 2018–2019, as Yahoo shows a rank next to ADP), with the round that rank implies (**Rd**), ADP and the preseason projection (points a game); sort by Rank, ADP or Proj. Press **Draft** next to a player on your turn, or auto-pick one pick or the rest; the CPUs pick in between. **The week loop:** the one primary button at the top says what comes next. **Play week N** scores the week with the real results; then every unrostered player is on waivers, you put in claims on the Players tab (with a bid in a FAAB league), and **Sim to Wednesday** settles them (by priority, or by bid in FAAB) and lets the CPUs move; then lineups, free-agent adds and trades are open again until you play the next week. **Tabs:** My Team (set your lineup with Move, drop players, look at any team or past week), Matchup (your game and the week's scoreboard), Players (the waiver wire with each player's usage over his last 3 games, adds and claims; Show starts on available players, and typing a name searches every player, rostered ones with their team, until the box is cleared), Trades (players CPUs are shopping to you, CPU offers with each manager's message, and a builder that shows the CPU's verdict and mood before you propose) and League (standings, schedule, scoreboard, activity, and the league chat where the CPU managers talk after every week). The regular season runs until the playoff weeks you chose; then the top seeds play a title bracket (top seeds get a bye when it has 5–7 teams) and everyone else plays for the places below. **No hindsight:** nothing on screen comes from a week you have not played. Before a week is played you see only its pre-game projection; points, records and standings stop at the last week played, the My Team and Matchup week pickers end at the current week, and later weeks on the League scoreboard show the pairings only |
| **Settings** | League scoring, size, superflex, and TE premium (used everywhere); connect Yahoo; check integrations. **System** shows when the auto-update last ran and what it did, Odds API credits left, data freshness and the last backup, with **Back up now**. **API access** takes the token when the backend has `API_TOKEN` set |

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
2. `base = w × rate + (1 − w) × prior`, where `w = games / (games + 4)`. The prior is
   last season's PPG if the player had at least 4 games, otherwise the position's
   replacement level. Two adjustments follow:
   - **Returning starters:** a fill-in's season rate was inflated by the games he played
     while a starter was out. When that starter is due back, the model takes off his measured
     gain (with/without, 2+ games) × the share of his games played without the starter × the
     share of remaining games the starter should play. Example: Woody Marks's model rate
     drops from 9.1 to 8.4 with David Montgomery due back. A starter out for the year
     leaves the job to the fill-in.
   - **Rookies** (no games last season) get +3%. Over 2023–25, rookie RB/WR/TEs kept 95%
     of their first-half scoring in the second half, against 92% for veterans (54 vs 389 players).
3. `model = base × remaining-schedule factor`: the average adjusted defense factor (below)
   of the opponents left on the player's schedule. The Rankings page shows it as **Sched**.
4. **Consensus blend:**
   `ros_ppg = 45% model + 35% Yahoo ROS experts + 20% this week's ESPN/props projection`,
   re-weighted when an input is missing.
   - Expert ROS ranks (Yahoo's 40+ expert consensus and Josh & Hayden) become points:
     ROS rank N gets the Nth best model projection at the position.
   - Injured players are scaled by the share of value they keep (see **Injury discount** below).
     **IR with a torn ACL or Achilles is season-ending: no rest-of-season value** (De'Von Achane
     drops to 0).
   - Yahoo publishes each week's ranks gradually. Each player uses his newest rank from the
     last two weeks, in your scoring format or the nearest one, so a half-published week
     doesn't erase everyone else's expert input.
   - Hover a ROS PPG value to see its inputs.
   - Example: Jonathan Taylor (standard) goes from model 17.9 to 15.6 (RB7) because the experts
     rank him around RB10 and ESPN projects 12.7 this week.
   - The blend can't be backtested (there's no stored history of expert ROS ranks). The model
     part is backtested below.

### Adjusted strength of schedule
Raw "fantasy points allowed" flatters a defense that happened to face weak offenses. For
each position, the app compares what every offense scored against a defense with what that
offense usually scores, and judges each offense's "usual" against the defenses *it* faced.
Alternating those two steps four times gives each defense a factor: **1.15 at WR = offenses
score 15% more WR points than usual against it.** Factors are shrunk toward 1.0 with four
games of neutral evidence and kept between 0.70 and 1.35, so a two-week sample can't
produce an extreme matchup.

### Injuries and vacated opportunity
- **Where status comes from, newest first:**
  1. **Sleeper's** live tags (Out, Doubtful, Questionable, IR, PUP, suspended), refreshed hourly.
  2. The **official weekly injury report** from nflverse: the game status plus the final
     practice (DNP, LP, FP).
  Veteran rest days aren't treated as injuries.
- **Chance to play** is learned from 2021–2025 reports: how often a player who recorded stats
  the week before played again with that tag and practice status, compared with healthy
  full-practice players.

| Status, final practice | Played |
|---|---|
| Questionable, DNP | about 49% |
| Questionable, limited | about 78% |
| Questionable, full | about 85% |
| Doubtful | 0–5% |
| Out | 0% |

- **Vacated work.** A player likely to sit leaves his expected points to teammates:
  - Each position keeps a share (75% of a running back's work stays with the RBs; WR work
    spreads to WRs and TEs).
  - The next player on the depth chart gets the largest cut.
  - When the season already has 2+ games without him, each teammate's *measured* change in
    expected points in those games is used, shrunk toward the heuristic for small samples.
  - Example: with Breece Hall Questionable, Braelon Allen gets **+7.5 points if Hall sits**,
    measured from his 3 games without Hall. Weighted by Hall's 22% chance of sitting, that
    adds +1.7 to Allen's projection.
- **What gets shown.** The projection is the if-active number times the chance to play.
  Each "If <teammate> sits" scenario shows the full upside.
- **Avoiding double counting.** If a teammate is already ruled Out, experts and Vegas have
  priced it in, so only the model's share of the boost is added. The model's share is cut
  again by the part of the player's rate built in games without that teammate (recency
  weights, shrunk like the rate): a fill-in with four straight games without the starter
  already has most of the bigger role in his rate. In the point-in-time backtest this cut
  RMSE 6.74 -> 6.70 (2023), 6.91 -> 6.87 (2024) and 6.76 -> 6.66 (2025), with rank
  correlation up each season.
- **Backup quarterbacks sitting move nothing.** A QB behind the other on the depth chart (when
  it lists both), else with fewer passing yards a game for the team (mop-up duty only), leaves
  him no vacated work, so the starter isn't boosted when his backup is inactive. A starter who
  sits still moves his work to the backup, even one who has started more games since he got
  hurt.
- **Backup quarterbacks.** A backup QB only plays when everyone ahead of him on the depth
  chart (Sleeper's, else nflverse's) sits. His chance to play is the chance they all sit,
  with a 2% floor for QB2 (in-game injuries), and his rest-of-season share is one minus the
  starter's, floored at 3%. Example: Drew Lock scored well while Sam Darnold was out, but
  with Darnold healthy he projects **0.2** this week (12.5 if he started) and 0.45 per game
  rest of season.

### Backtest and tuning
`python -m services.backtest --season 2025` replays a past season week by week, using only
information available before each game: stats so far, defense strength from earlier games,
that week's Vegas lines and injury report. It scores projections against what happened
(a player who sits scores 0).

| Weeks 4–18 | 2025 MAE | 2025 rank corr. | 2024 MAE (out of sample) | 2024 rank corr. |
|---|---|---|---|---|
| Old model (season-long rate) | 6.81 | 0.359 | 6.69 | 0.394 |
| Current model | **6.01** | **0.502** | **6.05** | **0.526** |
| Current model without the injury layer | 6.65 | 0.384 | 6.63 | 0.415 |
| … without recency weighting | 6.08 | 0.495 | 6.09 | 0.514 |
| … without Vegas implied totals | 6.06 | 0.501 | 6.06 | 0.525 |
| … without schedule strength | 5.99 | 0.504 | 6.03 | 0.528 |

The settings were tuned on 2025 and then checked on 2024 without re-tuning; the gains carry over
(about 10–12% lower error, rank correlation up about 35–40%). The replay uses each player's team as
of that week, no live data or current depth charts, and play probabilities learned with the tested
season left out.

The grid search (`--ablation` for the table above) chose these settings:
- **Recency:** a half-life of 4 games (a game four weeks ago counts half).
- **Snap-share trend:** the rate is multiplied by `1 + 0.10 x trend`, where trend = the last 3
  games' average snap share / the season's average - 1 (capped at +-1, 0 before 5 games or
  without snap counts). A WR whose snaps rose from 60% to 78% over the last 3 games gets about
  +3%. "Why?" mentions it under Recent production when it moves the rate 1% or more. One weight
  for every position (`USAGE_TREND_WEIGHT` in `services/weekly_model.py`), least squares on
  2021-2025 backtest player-weeks; `--ablation` includes a run without it.
- **Vegas:** implied team total^0.5.
- **Schedule strength: 0.5.** Inside the model it adds nothing beyond Vegas lines, which
  already price the defense. It's kept at 0.5, at a cost of about 0.02 MAE, because schedule
  strength was requested as a factor.

The run also writes `services/projection_ranges.json`, the source of the floor/ceiling ranges:
the 10th/90th percentile of actual ÷ projected points.
- It's split by position and projection tier (under 8, 8–12, 12–16, 16+ points), since stars
  have steadier floors than fringe players.
- It only counts games with 50%+ of snaps, so the floor answers "if he plays his normal role".
  Early injury exits are a separate risk.
- Example: a WR projected for 16+ has a floor of about 0.40× his projection (a 20-point
  projection floors near 8, not 2).

### Usage signals tested (2026-11)
Each candidate was fit on earlier seasons only and scored on the next one (2023 fit on 2021–22,
2024 on 2021–23, 2025 on 2021–24), same player-weeks as the production backtest. Kept only if
RMSE and rank correlation both improved in 2024 and 2025 (and not worse in 2023).

| Candidate | 2023 RMSE / rank r | 2024 RMSE / rank r | 2025 RMSE / rank r | Result |
|---|---|---|---|---|
| Production before | 6.703 / 0.6321 | 6.871 / 0.6314 | 6.660 / 0.6457 | |
| Actual vs expected points weight (now 60/40; fitted 0.6, 0.6, 0.7) | 6.703 / 0.6321 | 6.871 / 0.6315 | 6.662 / 0.6458 | dropped: 60/40 is already the fitted optimum |
| Same, per position | 6.702 / 0.6323 | 6.874 / 0.6303 | 6.663 / 0.6455 | dropped |
| Faster half-life for expected points | 6.705 / 0.6321 | 6.875 / 0.6304 | 6.661 / 0.6451 | dropped (training picked a slower one) |
| **Snap-share trend, one weight** | 6.703 / 0.6323 | **6.866 / 0.6317** | **6.658 / 0.6468** | **kept** |
| Snap-share trend, per position | 6.708 / 0.6291 | 6.866 / 0.6321 | 6.654 / 0.6471 | dropped: 2023 worse, weights flip sign |
| Target-share trend | 6.711 / 0.6302 | 6.873 / 0.6316 | 6.657 / 0.6464 | dropped |
| Carry-share trend | 6.701 / 0.6320 | 6.869 / 0.6316 | 6.659 / 0.6459 | dropped: within noise |
| Air-yards-share trend | 6.705 / 0.6316 | 6.865 / 0.6317 | 6.658 / 0.6459 | dropped: 2023 worse, sign says mean reversion |
| WOPR trend | 6.710 / 0.6306 | 6.871 / 0.6310 | 6.657 / 0.6467 | dropped |
| Opportunity (carries + targets) trend | 6.715 / 0.6291 | 6.880 / 0.6305 | 6.661 / 0.6455 | dropped |
| Expected-points trend | 6.713 / 0.6296 | 6.895 / 0.6291 | 6.661 / 0.6456 | dropped |
| NGS separation (WR/TE) | 6.702 / 0.6312 | 6.872 / 0.6330 | 6.660 / 0.6461 | dropped |
| NGS rush yards over expected per carry (RB) | 6.708 / 0.6319 | 6.862 / 0.6322 | 6.669 / 0.6455 | dropped |

Trends are the last 3 games' average over the season's average, minus 1. The snap trend's gain is
small (about 0.1% RMSE; a week-clustered bootstrap puts it below zero in 100% / 84% of draws for
2024 / 2025), so it is a nudge, not a new signal. Expected points already carry most of what the
usage shares know. FTN route participation isn't loaded, so it wasn't tested.

### Model adjustments: calibration, EPA matchup, weather
`python -m services.projection_factors --fit` refits these and prints how they do on held-out seasons.
- **What it does:** multiplies the model's number, and only the model's (props and experts
  already price matchups and weather), by `1 + b . x` per position:
  - **Calibration:** the raw model runs 3–12% high depending on position.
  - **EPA matchup:** the opponent's EPA allowed per dropback and per run, and its sack rate,
    from data before the week.
  - **Weather:** wind over 10 mph and cold under 40°F outdoors, and domes.
- **Weather data:** recorded conditions for past games come from nflverse. Upcoming outdoor
  games get a free Open-Meteo forecast for kickoff at the home stadium (`services/weather.py`;
  refreshed with the other weekly sources). Neutral-site games are skipped.
- **How it was fit:** least squares, so projections stay expected points (a median fit
  "improves" MAE just by shading every projection down). The fit only ever sees seasons
  before the one it's scored on.
- **Effects that showed up:**
  - Wind hurts QBs and TEs the most, cold helps RBs, domes help WRs, and a strong pass rush
    hurts QBs.
  - The EPA terms come out slightly negative: schedule strength and Vegas totals already
    lean on matchups, and EPA pulls back the overshoot.
  - **Tested and dropped:** red-zone role (expected points already carry expected TDs), game
    script and the game total. None helped in both test seasons.

| Held-out season | RMSE | MAE | Rank correlation |
|---|---|---|---|
| 2024 (fit on 2021–23) | 6.96 → **6.89** | 5.35 → **5.18** | 0.630 → **0.631** |
| 2025 (fit on 2021–24) | 6.80 → **6.69** | 5.20 → **4.99** | 0.638 → **0.642** |

(Refit with the snap-share trend and the current injury layer. The first fit, on the older model,
went 7.71 → 7.55 and 7.65 → 7.45 RMSE; the "before" model has since improved, so there is less
left for the factors to fix.)

Most of the gain is calibration (getting the level right). The matchup and weather terms add
a little to ranking; Vegas totals already price in most of what they know.

**Overfitting check** (first fit). Each season is scored by a fit on earlier seasons only. The held-out
gain (−0.19 to −0.21 RMSE) matched the in-sample gain (7.74 → 7.53 on 2021–24), and the
results barely move between ridge 0 and 2000, so the few adjustment terms are not memorizing
noise. The production ridge (20) stays. Learned source weights move in small capped steps.

### Power rankings and playoff odds

`services/league_insights.py`, read live from Yahoo (standings, the season's adds and trades,
and the scoreboard for every regular-season week so far and to come), cached for an hour:
- **Strength** is each team's best QB/RB/WR/TE lineup in rest-of-season points per game.
- **Weekly best ball:** for every remaining week, each team plays its best possible lineup from
  its whole roster (not IR), with every player projected against that week's opponent. A player
  on bye is out, and for each one the team gets that week's best free agent at his position (a
  rough waiver pickup), so byes cost only the gap to the waiver wire.
- **Playoff odds:** the rest of the regular season is simulated 10,000 times. Each simulated
  season gives every team one strength offset (sd 9 points: rest-of-season estimates miss by
  about that much, and the miss lasts all season), and every starter can get hurt each week
  (QB 8.8%, WR 5.4%, RB/TE 4.3%; once out, he stays out the next week 60% of the time; a player
  hurt now starts out for his expected games missed, see **Injury discount**), his spot going to
  the best free agent. Each game a team scores
  Normal(that week's projection + offset − injuries, 21 points). Wins and points for are added
  to the real standings; the top `playoff_teams` (Yahoo's setting) make it.
- **Tendencies:** "Active trader" (2+ trades), "Waiver regular" (1.5× the league's median
  moves), "Quiet" (2 moves or fewer, no trades), plus the positions each manager has traded for
  and picked up.
- **All-play and luck** (`services/all_play.py`, from Yahoo's scoreboard for every completed
  week, each finished week fetched once): every week each team's score is compared with every
  other team's score that week, giving its all-play record (e.g. 52-33). All-play expected
  wins = the sum over weeks of (teams beaten + half the ties) / (teams − 1); **luck** = actual
  head-to-head wins in those same weeks − all-play expected wins (positive: the schedule gave it
  more wins than its scores earned). If Yahoo's scoreboards can't be read, the rankings load
  without luck.
- **Manager tags** (`services/manager_tags.py`), each shown with its evidence: "FAAB whale" (average winning bid ≥ 12% of the budget), "Sits on FAAB" (spent
  under 25% of the budget by week 6 or later, only when Yahoo reports the FAAB balance; both FAAB
  leagues only), "Waiver hawk" (1.5+ moves
  per week played), "RB hunter" etc. (50%+ of at least 4 adds and trades at one position),
  "Trade shark" / "Overpays" (won / lost 2+ graded trades by 10+ points).
- **Past trades** (`services/trade_grades.py`): every completed trade this season (trades are
  read separately from the latest 200 adds, page by page, so none are missed), graded after
  the fact. For each side, the points (your league's scoring, from the nflverse weekly stats in
  the database) its received players scored in the weeks after the trade, minus the same for the
  players it sent. Counting starts with the first NFL week that kicked off after the trade, and
  only weeks with stats loaded count. A side ahead by 10+ points wins the trade. Players we can't
  match to our stats (and kickers / defenses) are skipped and listed.

### Floors, ceilings and the matchup simulation

**Ranges** (`services/score_ranges.py`). A player's floor and ceiling are the 10th and 90th
percentiles of how players with his projection actually scored, if he plays:
- Fit on every active game of 2021-2025 (low-snap games and zeros included, so busts count),
  as quantiles of actual ÷ projection by position and projection tier (under 4, 4-8, 8-12,
  12-16, 16+ points), scaled 5% toward the median because the blend misses less than the
  model alone.
- **Shape:** running backs and pass catchers who score mostly on touchdowns (last four games)
  get a lower floor; deep targets (high aDOT) a higher ceiling and a lower floor.
- On 2026 (data the fit never saw), 7% of players finished below their floor and 11% above
  their ceiling, against a 10% target. The old ranges, fit only on full-role games, had 31%
  below the floor.
- The Accuracy view scores this season's saved projections against today's ranges (without
  the shape adjustment, which isn't saved): weeks saved before a refit carry the old floors.

**Matchup simulation** (`services/matchup_sim.py`). Each starter's score is drawn 20,000 times
from his own skewed range, plus a separate draw for whether he plays (so injury risk is counted
once). Players in the same game move together, with correlations measured from 2021-2025 box
scores (a Gaussian copula):

| Same team | | Opponents | |
|---|---|---|---|
| QB–WR | +0.32 | QB–QB | +0.17 |
| QB–TE | +0.29 | DST vs the QB it faces | −0.375 |
| K–DST | +0.27 | DST vs that team's K | −0.31 |
| QB–RB | +0.08 | DST vs that team's RB | −0.19 |

So stacking your QB with his receivers widens your range (good as an underdog), and starting a
defense against your own quarterback costs you when he plays well. Win chances show to the
nearest 5%.

**How close a call is.** Start/sit picks by projection gap were right 51% of the time under 1
point, 56% at 1-2, 58% at 2-3 and 75% over 5 (2021-26). Lineup changes under 1.5 points are
labelled **Toss-up**, 1.5-3 **Lean**. Upside or safe is recommended over the most-points lineup
only when it wins at least 3 points more often; smaller edges show as **Marginal**.

### Game day: locks, warnings, chance to play
- **Locks** (`services/game_locks.py`): once a player's game kicks off, his points are fixed and
  he can't be moved; advice only reshuffles unlocked players. The latest-kicking FLEX-eligible
  starter goes in FLEX, which keeps a late swap open.
- **Warnings:** a starter under 90% to play gets a note with his kickoff, when inactives come
  out (about 90 minutes before), and whether a bench player at his position plays later.
- **Untagged players still sit sometimes** (`services/injuries.py`): by last week's snap share,
  e.g. a running back under 10% sits 24% of the time; a player who was inactive last week plays
  only 34% of the time ("Check inactives"). Measured 2021-26.

### Your league: waivers, trades, streaming
**Lineup gain** (`services/lineup_gain.py`). Moves are scored by how many points they add to
your best lineup over the rest of the season: each week's best QB/RB/WR/TE lineup before and
after, weighted 0.95 per week ahead in the regular season and by your playoff odds in the
playoff weeks. Each player's week is his healthy rate × that opponent (0 on a bye); a player
hurt now sits out his expected games missed and his first games back carry the post-injury dip
(see **Injury discount**), and healthy weeks carry the weekly injury risk (QB 8.8%, WR 5.4%,
RB/TE 4.3%).

**Waivers** (`services/waiver_advice.py`, My Team → Waivers). Every free agent is scored by
lineup gain after dropping your least useful player (never your starter's handcuff unless the
add is worth twice as much). What to do depends on the league:

| League type | Advice |
|---|---|
| FAAB | A bid: the league's typical winning bid for the k-th most wanted player (from Yahoo's transactions), scaled by rivals' balances and how many of them need the position, capped by what he's worth to you, the richest interested rival + $1, and half your balance. Before 8 winning bids exist it's a labelled estimate (rank 1 15% of the budget, 2 8%, 3 5%, 4-6 3%, else 1%; ×1.3 early, ×0.6 late). Each bid also shows its share of the league's original budget ("$14 · 14% of budget") |
| Rolling priority | Each claim shows the earliest priority it's worth ("worth #N priority or later"): #1 for a difference-maker (8+ points and the most wanted, a handcuff to your injured starter, or a starter for 3+ of the next 4 weeks), otherwise N = his rank by lineup gain among this week's useful claims, but never inside the top 3 and never past the number of teams. Claim if your priority is #N or later (always with the last priority), else wait. Teams ahead of you that need the position are named |
| Free agency | Add now, best gain first |

Gains under 1 point are marked marginal.

**Trades** (My Team → Trades). For each real team, up to 15 value-fair candidates (from the trade
finder, built from active roster spots; IR/NA players aren't offered or targeted) are scored by
both starting lineups before any are cut. An offer is kept only if it adds to your lineup and costs
theirs at most half a point per weighted remaining week. Roster spots count: in a 2-for-1 the team
getting two players drops the one it misses least ("they drop X"), and if your roster was full the
spot you open is filled by the best of the top 10 free agents ("you add Y"); both are in the gains.
Each offer (and a calculator trade scored on a league) carries a `lineup_label` from the signs of
both gains, with gains within ±0.5 points counted as even: "Helps both lineups", "Helps you, costs
them", "Helps them, costs you", "Hurts both" (or "... even for them" / "About even for both").
Offers rank by your gain minus half of what they lose (`offer_score`), so a deal that helps both
lineups beats a slightly bigger one that hurts them; at most three per team,
no more than two built on the same player of yours. After the league's trade deadline the list is
empty. The standalone calculator discounts extra players more steeply (1, 0.7, 0.5, 0.4) since
depth is worth little; pick a league in the calculator ("Score on") to also see both lineups' gains,
scored exactly as on this list. If some players you give aren't on your roster there (or some you get
already are), the value verdict still shows with a note instead of lineup gains.

**Streaming** (My Team → Streaming): your defense (and quarterback, in one-QB leagues) and the
best ten free agents over the next three weeks, with a plan that only switches for 2+ points.

**This week** (Dashboard): lineup changes and toss-ups, game-day warnings, the best pickup and
the next lock.

### Kicker and defense projections
`python -m services.kdst_model --fit` refits these. Both use team profiles (rates shrunk
toward the league with four games of league-average evidence) built only from games before
the week.

**Kicker**
- **Field-goal tries:** from the team's Vegas implied total, its red-zone TD rate
  (stalled drives become field goals) and the opponent's red-zone TD rate allowed (stop %).
- **Distance and makes:** attempts split into 0-39 / 40-49 / 50+ yards by the league mix.
  Make rates are the kicker's own, shrunk toward the league with 8 attempts of evidence,
  then lowered for wind and cold.
- **Extra points:** expected TDs × his PAT rate.

**Defense**
- **Sacks:** the opponent's dropbacks × a log5 blend of this defense's sack rate and the
  opponent's sacks allowed. Takeaways work the same way against the opponent's giveaways.
- **Return TDs:** a rate per takeaway.
- **Points allowed:** the opponent's implied total plus the historical spread of
  outcomes, scored on the points-allowed tiers.

**Tested and left out:** the 4th-down go rate and defensive EPA per play didn't beat Vegas
totals in both held-out seasons.

| Held-out season | Kicker MAE (model vs season-to-date avg) | Kicker rank corr. | Defense MAE | Defense rank corr. |
|---|---|---|---|---|
| 2024 | **3.61** vs 4.01 | **0.24** vs 0.06 | **4.03** vs 4.60 | **0.39** vs 0.12 |
| 2025 | **3.76** vs 4.07 | **0.16** vs 0.09 | **4.20** vs 4.85 | **0.31** vs 0.10 |

Week X has **Kickers** and **Defenses** views (Yahoo default scoring). My Team and Matchup
score kickers and defenses with each league's own rules, imported from Yahoo.

### Breakout and regression flags

| Flag | Meaning |
|---|---|
| Opportunity spike | A teammate may sit and this player would inherit 3+ pts of his work |
| Role growing | Expected points or snap share rising over the last 3 games |
| Snap share climbing | Last 3 games' snap share 10+ points above his earlier games, latest 50%+. Skips players already scoring 18+ (TE 13+). Snaps stand in for route share, which isn't free in-season |
| Efficient part-timer | Not yet a fantasy starter, on a part-time role (RB under 55%, WR under 70%, TE under 65% of snaps), but scoring at a starter's pace per snap |
| TDs due / TD-heavy | Touchdowns 2+ below or above what his looks predict |
| Under/over-producing usage | Scoring 3+ pts/game away from what usage predicts |
| Competition returning | A starter who missed the last two games is back. The fill-in's measured gain without him (2+ games) × his chance to play is shown and taken out of the model's share of the projection. A backup QB returning never flags the starter |
| Injury risk | His own chance to play is under 90% |

### Week X rankings
Each player's week projection is a weighted blend of four source groups. These are the
starting weights; they're refit after every completed week (see **Learning the weights**).

| Source | Starting weight | How it becomes points |
|---|---|---|
| Vegas player props | 30% (never under 15%) | Yardage lines and over/under prices → expected yards; receptions, pass TDs, INTs → Poisson means; anytime-TD price → expected TDs. Scored with your league settings. Missing markets (e.g. an RB's receiving line) are filled from the model |
| Experts (Yahoo) | 35% | Yahoo's 40+ ranker consensus and Josh Norris & Hayden Winks, each rank converted to points, then averaged. They're correlated Yahoo opinions, so they blend as one source (both still show) |
| Matchup model | 20% | `base × defense factor × (Vegas implied team total ÷ team's scoring average)^0.5`, keeping the shape of the player's stat line |
| ESPN | 15% | ESPN's projected stat line, scored with your league settings |

- Weights re-balance over the sources a player has (a backup with no props line is
  blended from the rest).
- **Ranks to points:** rank N at a position gets the Nth best numeric projection (props,
  ESPN, and model blended) at that position, so "RB3" means RB3-level points. Each expert's
  rank is converted first and the points averaged (averaging ranks first would understate a
  player two experts disagree on).
- **Extra schedule tilt:** the blended projection is multiplied by `defense factor^0.25`.
- Players on bye are skipped. When outside sources have published the week, players none
  of them cover are left out too (usually injured or inactive).
- **Props timing:** each game's props are bought once, within 2 hours of kickoff, after
  Sunday-morning injury news (the background check looks every 15 minutes on game days).
  When the month's Odds API credits can't cover every game left, only games with one of
  your rostered players are bought, plus every one-game Thursday and Monday slate.

**Chance of N+ points** (`services/threshold_odds.py`). Each row carries `threshold_odds`,
the chance of at least each of three totals: QB 15/20/25, RB and WR 10/15/20, TE 8/12/16
(kickers and defenses have none). The Proj column shows the highest one ("20+ pts: 31%",
the rest in its tooltip) and **Why?** lists all three.
- **Math:** `chance to play × P(points ≥ N if he plays)`. The second part reads the same
  7 quantiles (5th–95th percentile) behind the floor and ceiling as one piecewise-linear
  distribution, the one the matchup simulation draws from (tails down to 0 and past the
  95th). So a healthy player's ceiling is always a 10% shot, and the odds, the range and the
  simulation agree.
- **Calibration** (`python -m services.threshold_calibration --season 2026`): saved
  pre-kickoff projections for weeks 1–7 of 2026 (8,262 player-week thresholds, PPR; the
  ranges were fit on 2021–2025, so those seasons are refused as in-sample; a player who sat
  counts as 0, and players already ruled out are left out of the scores). Predicted 15.3% on average,
  hit 15.4%; Brier score 0.092 vs 0.130 for always guessing the base rate.

  | Predicted | 0–10% | 10–20% | 20–30% | 30–40% | 40–50% | 50–60% | 60–70% | 70–80% | 80–90% |
  |---|---|---|---|---|---|---|---|---|---|
  | Happened | 2.3% | 15.3% | 22.4% | 39.2% | 42.1% | 55.4% | 61.4% | 76.4% | 90.2% |
  | Rows | 4,741 | 1,134 | 769 | 429 | 480 | 314 | 228 | 123 | 41 |

  By position the average prediction is within 0.4 points of the hit rate (QB 17.2 vs 16.9%,
  RB 18.2 vs 18.3%, WR 15.4 vs 15.4%, TE 11.8 vs 12.2%).

**Grading the props** (`services/props_calibration.py`). Stored prop-derived stats are
compared with the box score (players who sat are skipped: their props were void; a player
with offensive snaps but no box score played and counts as all zeros), and each
stat (pass yds, pass TDs, INTs, rush yds, receptions, rec yds, TDs) gets a factor
`1 + n / (n + 150) × (actual ÷ expected − 1)`, capped at 0.8–1.2, and 1.0 under 30 pairs.
Each week's factors only use earlier weeks. `python -m services.props_calibration --season 2026`
reports the error of prop-implied points (on the stats the props cover) with and without
the correction, on weeks the factors never saw.
- **Status: off** (`ENABLED = False`). Props are stored for weeks 7–8 of 2026 only (323
  player-weeks where the player played, 23 of them in week 8 at the time of the check). Week 8
  with factors learned from week 7: RMSE 4.13 → 4.10 points, too few rows to tell from noise.
- So far the props ran short on yardage (rush yds +5.7 per player, rec yds +2.9) and about
  right on TDs and receptions, as expected when a median line stands in for a right-skewed
  mean. Turn it on once a few more weeks show the gain holds.

**Saved locally.** Two tables in the local SQLite database (never committed) store the results:
- **`rankings_snapshots`:** the computed week, keyed by league settings, week, every data input
  and a hash of the model code. A change to any of them recomputes; otherwise a load (even
  after a restart) takes about 0.1 s instead of several seconds.
- **`projection_history`:** each player's last projection before kickoff, with every source's
  points. Once that week's stats are loaded, `python -m services.projection_eval --season 2026`
  reports the blend's and each source's error.

**Actual points.** Once a player's game is final, the Week X table shows what he scored, in
your scoring, next to the projection. It's green if he beat the projection by 20%+, red if he
missed it by 20%+, and "DNP" if his team played without him. Past weeks keep the weights they
were projected with, so you see the projection as it stood, not one built with hindsight.

**Learning the weights** (`services/weight_learning.py`). After each completed week the
weights are refit on every week so far (a week's weights only ever use earlier weeks):
1. **Rows:** fantasy-relevant players only (the top 36 projected at each position that week,
   top 24 at QB) who played, with each source group's points and the actual points.
2. **Fit:** least squares shrunk toward the starting weights as if those were backed by 1,500
   player-weeks. The sources are 0.9+ correlated, so an unshrunk fit swings wildly week to
   week (the old per-week fit put 0% on the model one week and 69% the next). The penalty is
   scaled by how far the sources sit apart within a row, not by the points level: scaled by
   the level, 1,500 "rows" really weighed like ~50,000, and 1,155 player-weeks moved the
   weights by 0.05 points.
3. **Limits:** props never below 15% (their value is late news the history can't show yet),
   every other group at least 5%. Under 300 rows, the starting weights stand.

Refit on 2026 weeks 1-10 the weights move to about props 25, experts 53, ESPN 17, model 5
(the model is at its floor). Learned week by week (each week's weights from earlier weeks
only) they cut next-week MAE 5.82 -> 5.78 and RMSE 7.29 -> 7.26 over weeks 3-11 against the
starting weights. Yahoo's consensus alone is about as accurate as the blend, so the Accuracy
view shows them side by side.

**Backfilled weeks** (`services/history_backfill.py`). Weeks 1–6 of 2026 were rebuilt
after the fact, without hindsight:
- Yahoo's weekly ranks and ESPN's projections are re-pulled; Yahoo and ESPN still publish
  them for past weeks. Vegas props aren't backfilled, since historical odds cost credits.
- The model only sees games before each week (stats truncated, defenses and EPA profiles
  rebuilt from earlier games), injuries come from that week's official report, and the
  blend uses only weights learned from earlier weeks.
- The weights are then re-learned week by week, in order. Rows saved live before kickoff
  are never replaced.

Yahoo's past data has Hayden Winks' ranks for weeks 1–2 only, and Josh Norris isn't in its
2026 data at all, so the Josh & Hayden source is thin for those weeks.

Average error per player-week (rows in parentheses):

| Week | Props | Josh & Hayden | Model | Yahoo | ESPN | Blend |
|---|---|---|---|---|---|---|
| 1 | — | 5.73 (216) | — | 4.60 | 4.73 | 4.57 |
| 2 | — | 5.54 (195) | 4.94 | 4.07 | 4.41 | 4.07 |
| 3 | — | — | 4.83 | 4.43 | 4.86 | 4.44 |
| 4 | — | — | 4.84 | 4.34 | 4.62 | 4.31 |
| 5 | — | — | 5.09 | 4.49 | 4.64 | 4.52 |
| 6 | — | — | 4.71 | 4.10 | 4.31 | 4.08 |
| 7 (live) | 5.12 | 5.56 | 4.31 | 4.23 | 4.57 | 4.20 |

Yahoo consensus had the lowest error of any single source in most weeks. Run the backfill again with
`python -m services.history_backfill --season 2026 --weeks 1-6` (`--no-fetch` to rebuild
from the sources already stored).
- **Running it:** it updates on its own every week while the API runs: the background
  check loads the finished week and the load runs the update. The check also saves the next
  week's pre-game projections, so the learner has data even in weeks you never open Week X.
  `python -m database.populate_stats` does the same by hand.
  `python -m services.weight_learning --season 2026 --dry-run` shows what it would change.
- **Storage:** each update is a row in the local `learned_weights` table. Delete the rows to
  go back to the starting weights.

On Week 7 of 2026 (before props were configured), the blend agreed closely with Hayden's
own ranks (rank correlation 0.93 for the top 24 RBs, 0.97 for the top 36 WRs) while still
moving players on matchup.

### Value over replacement and trade value
- **Replacement level** depends on your league: starters league-wide (teams × slots, with
  FLEX and superflex filled by the best remaining players) plus each position's share of bench spots.
  The next player down is the "waiver-wire" baseline. League size and superflex change values,
  never projected points: a player with no prior season shrinks toward the replacement level of
  a 12-team, 1-QB version of your league (a test holds this invariant).
- **Checked against a fixed per-team depth** (`python -m services.replacement_eval --seasons
  2021-2025`, point in time from weeks 4 and 8): GWTTKB's depth {QB 1.17, RB 2.83, WR 3.67,
  TE 0.75 per team} ranks all players' rest-of-season VORP better when the many zeros count
  (PPR 12 teams: Spearman 0.47 vs 0.41 against our own target), but worse for the players who
  ended up mattering (0.505 vs 0.519), and worse in standard scoring. Since it doesn't win
  across the board, the replacement level is unchanged.
- **`trade_value = availability × max(0, healthy_ppg − replacement_ppg) × games_left`**:
  rest-of-season fantasy points above what you'd get from waivers.
  - `availability` is the share of remaining value he keeps (the injury discount below; for a
    backup QB, the games his starter is expected to miss) and `healthy_ppg = ros_ppg / availability`
    is his rate when he plays. An injured star keeps value for the games he will play: 18 ppg
    healthy, out half of 8 games, replacement 9 is worth 0.5 × 9 × 8 = 36, not 0.
  - An injured starting QB stays the starter even when the depth chart lists his fill-in first
    (the QB with the most 100-yard games this season is the team's starter); before, Jalen Hurts
    on IR read as a backup worth 0.
  - `games_left` counts his team's remaining games through the fantasy end week (week 18 by
    default), so a bye still ahead costs a week. Lineup gain keeps a backup's small share
    instead of projecting him as a starter.

### Injury discount
`services/injury_recovery.py`, fitted by `python -m services.injury_recovery_fit --seasons 2021-2025`
on 708 injury spells (a fantasy-relevant player missing a team game right after playing, until he
plays again; numbers in `injury_recovery.json`).
- **At the injury** the discount comes from the body part: on average a concussion costs 2.2
  games, an ankle or hamstring 2.6, a knee 3.0 (shrunk toward the 2.8 average). Injured reserve
  is at least 4 games from placement. **Out for the season: 0** (a torn ACL or Achilles tag, or the
  news says so, below).
- **Sleeper news overrides the averages** (`services/injury_news.py`). Each hourly status refresh
  re-reads the news of injured QB/RB/WR/TE players whose Sleeper news stamp changed and keeps
  only parsed signals, never the text:
  - "out for the season", "season-ending", "done for 2026", "miss the remainder of the season"
    → value 0 (Jalen Hurts, Jaxson Dart, Terry McLaurin). Negated, partial or speculative wording
    doesn't count ("avoids a season-ending injury", "a significant stretch of the remainder of the
    season", "could miss the rest of the season"), nor does "out for the season opener", nor a
    sentence about a teammate (Sleeper writes every player as "Name (injury)"; the nearest one must
    be his, and a ", who ..." clause must hang off his name).
  - a return timeline ("out 6-10 weeks", "3-to-4-month absence", "at least four weeks") replaces
    the body-part average, counted down from the day it was published. The return date is the
    median over his headlines, since later articles restate the first diagnosis, and a bye inside
    the window isn't a missed game. Davante Adams (6-10 weeks from Oct 19) keeps about 40% of his
    value.
  - only news since his last kickoff counts. Time already missed isn't a timeline ("after a
    three-week absence", "injured two weeks ago", "fourth missed game in five weeks"), and numbers
    must be whole words (not "26" in "2026").
  - new signals are merged into the stored ones (Sleeper returns only the newest ten headlines, and
    the first diagnosis matters); a failed player is skipped without stopping the others or the
    status refresh, and a news update invalidates cached values.
  - Sleeper's "Surgery" note without a timeline means at least 6 games (a product default; too
    rare in the injury reports to fit). Its notes also refine the body part ("Achilles" +
    "Tendinitis" is a soft-tissue injury, not a tear).
- **While he's out, the evidence moves it.** This week's practice report scales the games still to
  miss: practicing (limited or full) ×0.40, not practicing ×0.85, no report at all (mostly IR) ×1.22.
  Time alone doesn't predict a return in the data (31% of players are back after one missed game,
  only 17% after four or five: the long absences are the bad injuries), but long injuries often go
  weeks without news. So, as a product rule rather than a fitted number, the discount (measured
  against the season left when he got hurt) shrinks **10% for each game he's missed**: his value
  creeps back up week by week even without news.
- **After he returns**, players run low for a few games: the first game back about 87% of his rate,
  the next two 93% and 95% (relative to healthy players, which removes regression to the mean), and
  8 percentage points more of them are hurt again within five weeks. That counts as about half a
  game of lost value, then he's back to normal.
- Example, a knee injury with 10 games left and no news: worth 65% of his healthy value the week
  he's hurt, then 69%, 72%, 75%, 77%; a limited practice in week 2 jumps him to 82%; after he
  returns, 90%, 95%, 98%, then 100%.
- **Special cases.** Doubtful counts as this week only (he usually sits it, and the fit only saw
  players who did miss time); a suspension is at least 4 games with no post-return dip. The value
  never keeps games he's sure to miss (this week, or what's left of IR), so late in the season an
  IR player with one game left is worth 0 in trade value, lineup gain and the season sim alike.
  Lineup gain and the season sim count his team's games, so a bye inside the absence isn't a
  missed game, and without a loaded schedule a reported timeline's weeks are taken as games.
- **Role on return.** A returning RB whose fill-in scored at least half his rate over 2+ games is
  flagged with the fill-in's name (Breece Hall with Braelon Allen). The value cut is the fitted
  `rb_role_loss`, which is **0** on 2021-25: returning RBs got their snaps and scoring back even
  after a strong fill-in (37 cases; WR and TE showed no effect either). A real role change shows up
  through the expert ROS ranks in the blend.
- The trade calculator shows the note ("Knee injury: likely out 2 more games · worth about 72% of
  healthy"; a returning RB: "may share work with Braelon Allen, who played well while he was out"), the
  same expected absence drives lineup gain (waivers, league trades) and the season simulation, and
  Ask AI's rankings rows carry it.

### Trade calculator
Each side is sorted by value and weighted **1.0, 0.7, 0.5, then 0.4** for every further player.
Lineup spots are limited, so two mid-tier players are worth less than their raw sum. Both sides
get the same treatment, so an even 2-for-2 stays even. Verdict from the percent difference:
**≤ 5% fair, ≤ 15% slight edge, more is lopsided**, except that a gap under 0.4 points per week
left is called fair and under 1.2 per week at most slight (between low-value players a few points
is noise, even at a big percent). Each player shows his position rank, rate, games left, chance
to play and schedule; the result also shows how expert consensus (the market) sees the deal.
"Balance it" suggests players not in the deal whose addition brings the trade closest to even;
when the trade is scored on a league (`league_id`), only players on the two rosters.

Two more lines explain the verdict:
- **Consolidation** (`consolidation`, n-for-m trades only): how far the package discount moves the
  gap, i.e. the adjusted difference minus the raw one. In a 2-for-1 the side getting the single
  player gains it: giving 100 + 60 for 130 is a raw 30-point loss but only 12 after the discount,
  18 points toward the consolidating side.
- **To even it** (`even_it`, any verdict but fair): the trade value one more player would need,
  added to the short side, to make the sides even after the discount (130 + 0.7 × 17 = 142 above:
  "ask for a player worth about 17"), with the closest balance suggestion named.

The agent's `evaluate_trade` tool returns the same two fields.

### Trade finder
1-for-1 and 2-for-1 offers for the players you'd move. An offer must be within −5%…+8% for you by
this app's values and −15%…+5% by market (expert-consensus) value, and the app must like it for
you at least as much as the market does: the edge comes from where this projection differs from
consensus. Offers rank by expected edge, your edge × a rough chance they accept
`1 / (1 + exp(−(their market edge + 0.02) / 0.04))` (about 62% for a deal the market calls even,
32% for one it calls a 5% loss for them); offers with no edge for you follow, likeliest first.

### Buy low / sell high
Per player (3+ games):
- **FPOE** = actual − expected fantasy points per game, both over the games that have expected points (the
  card's "PPG vs xPPG" uses the same games, so it can differ slightly from his season PPG)
- **TD luck** = actual TDs − expected TDs
- **Usage trend** = expected points over the last 3 games vs the season
- **Recent form** = last-3 PPG vs season PPG (other managers react to this)

**Buy low:** a valuable role (expected PPG at or above replacement), FPOE ≤ −1, usage not
collapsing. **Sell high:** the mirror image. Only players with positive trade value qualify.
Scores are a percent of expected points, so QBs don't drown out other positions.

### Player percentiles and what changed
Season stats are regular-season box scores (nflverse) and snap counts; shares are averaged over
the games that have them. The **pool** is every player at the position with 4+ games (half the
most games played anyone has, early in the season). Each stat also needs its own sample before it
counts: 100 pass attempts for QB rates, 40 carries for yards per carry, 20 targets for yards per
target, catch rate and aDOT, 50 touches for fumble rate, 3 games with share data for target,
air-yard and snap share. Players below a stat's sample are left out of that stat's pool, and
their own value shows "Small sample". The percentile is the share of the pool below him (ties
count half). For INT rate and fumbles lost per 100 touches lower is better, so the percentile
is flipped (90 = better than 90%). aDOT and QB air yards per attempt are **context**: shown
with a percentile but no good/bad color. Fantasy PPG uses your scoring settings.

**What changed** needs 5+ games: the last 3 against all earlier games this season. Big moves
are flagged at ±10 points of snap share, ±5 of target share, ±8 of air-yard share, ±2
targets/game, ±4 carries/game and ±5 pass attempts/game.

### Odds page
Read-only: it never calls The Odds API (500 credits a month). Lines are the closing
`spread_line` (home margin, positive = home favored) and `total_line` from the nflverse
schedule; implied team totals are (total ± spread) / 2, the same as Week X rankings. Player
props are the rows `weekly_refresh` already stored (source `vegas_props`): the expected stats
implied by each player's lines, bought once per game within 2 hours of kickoff. The page shows
when each game's props were fetched relative to kickoff.

### Replay League
A game against the computer on a past season (`services/replay_*.py`, API under `/api/replay`), or on a
simulated one ([Simulated seasons](#simulated-seasons-replay-league), below).
One rule runs through it: **no hindsight**. CPU managers and every number shown before a week is
played use only what existed then: preseason ADP (Fantasy Football Calculator, with MyFantasyLeague
for the deep end) and each week's pre-game projection (ESPN, Sleeper where ESPN has none). The
engine and the screens read the season through `SeasonData.as_of(week)`, a copy with nothing from
that week's games on; only scoring a week reads its real points.

- **Data:** `python -m services.replay_data --seasons 2019` loads the season's results (nflverse), ADP
  and weekly projections into `replay_adp` / `replay_projections`, and injury tags into
  `replay_injuries`, and snap counts into the shared `player_snap_counts_weekly` (the "snaps" step; every
  live reader filters by season and seasons before 2021 are never read live, so they cannot reach the live
  model; a failure is reported and never blocks the season). `GET /api/replay/seasons` says which
  seasons are ready (stats, schedule, K/DST, 180+ ADP players, and projections for every week of the
  regular season: one missing week would zero it). It also reports `injury_weeks` (weeks with tags
  stored), which `ready` does not require, and `last_week` (17 or 18: a week-18 final needs 18). A re-run of Prepare fetches the MyFantasyLeague ADP
  tail again while a season has none.
  **ADP per draft format:** `replay_adp` holds one list per `format`: `ppr`, `half`, `std` and `2qb`
  (Fantasy Football Calculator's 12-team PPR, half-PPR, non-PPR and 2-QB mock drafts, every season
  2018-2025; a player one list leaves out but another FFC list of the season has takes his ADP and spread
  from that list, PPR first, then the nearest format, because FFC's thinner lists drop top players (2026's
  half-PPR list had no Drake London; he went 13th in PPR; such a row is stored as source `ffc_borrowed` and never
  lent on again, and a QB the 2-QB list borrows from a 1-QB list moves to the pick where the average 2-QB
  list takes a QB the average list he came from takes at his pick, never before pick 1,
  `replay_superflex.borrowed_qb_adp`);
  MyFantasyLeague's PPR real drafts fill each list's
  tail past the last pick with the players no FFC list has). A league drafts
  from its own list (`services/replay_adp_format.py`): a superflex league (a SFLX slot or 2+ QB slots) the
  2-QB list (it also loads its scoring's list as `SeasonData.scoring_adp`: see superflex values below),
  otherwise its scoring's list, and custom scoring the preset nearest its points per reception
  (0.75 -> PPR, 0.25 -> half). The list is chosen once, at creation, and saved with the league
  (`Settings.adp_format`; leagues saved before there were several lists keep PPR), so a league never
  switches lists mid-season. A season missing that list uses the nearest one stored (2-QB -> the
  scoring's list; PPR <-> half <-> standard), and a superflex league without a 2-QB list places its QBs
  by the fitted average 2-QB list (`replay_superflex.json`). A re-run of Prepare fetches only the formats a season
  lacks. A format FFC cannot give is reported and leaves what is stored; when FFC answered without a list
  (rather than failing to answer) the format is remembered as having none and not asked for again until
  a forced Prepare. While MyFantasyLeague is down, a format with nothing stored is saved from FFC alone
  (its tail comes on the next run) and every stored format stays exactly as it is.
  The "experts" step (2020 on) stores FantasyPros position ranks from the nflverse archive in
  `replay_expert_ranks` (`services/replay_experts.py`): the last preseason cheatsheet and every in-season
  rest-of-season snapshot. Week W uses the latest snapshot taken before week W's first Sunday kickoff; the
  draft uses the last cheatsheet taken before week 1's first kickoff (the Thursday opener). A failed
  download or import is reported and never blocks the season; without ranks the values use the app's model
  ranks only ("model ranks" on the Trades screen).
- **Injuries and IR:** Q, D and O come from the official injury report; IR, PUP, SUS and EXE from the weekly
  roster status (PUP = status PUP or reserve code R04; suspended = SUS or R40/R33/R30; EXE = status EXE,
  the commissioner's exempt list, with no injury text (Josh Jacobs 2026 weeks 1-6); every other
  reserve list = IR, except the lists that are not an injury (retired R02, did not report R03, left squad
  R06, the 2020 opt-outs R62), which get no tag, like holdouts; the game-day inactive list is not used).
  A report from a club that is not on the player's roster rows that week is ignored (a player traded
  after his game). An O, IR or PUP tag without text takes last week's injury text unless that text names
  no injury (rest, personal, "not injury related"). A roster status wins over a report tag, and a hard
  tag is carried over the player's bye. O, IR, PUP, SUS and EXE mean "out" this week even if a stale
  projection exists; Q and D are information only.
  IR rules: one slot per team; a player is eligible by his tag only (O, IR or PUP: not a suspension or EXE,
  not just "no projection"); he does not count against the roster, can't start and can't be traded; he
  can swap places with another eligible player; a team whose IR player lost his tag can't add, claim or
  trade until it activates (dropping someone if full) or drops him, and a CPU in that state declines every
  trade. CPUs stash an injured player worth keeping, swap or cut to bring one back as soon as a week's
  tags are known (when the waiver phase opens, again at the waiver run and at the start of the week), and
  still count an IR player for their positional limits and their view of the rest of the season. A
  week's final report and roster status are shown from the start of that week (before its waiver run),
  like its projection. The draft shows only week 1's IR, PUP, suspension and exempt-list tags (`replay_pool.PRESEASON_TAGS`):
  those are preseason moves (2021: Gus Edwards' and J.K. Dobbins' ACLs), and a CPU drafts such a player at
  his chart value (the pick his discounted chart is worth, `replay_chart.chart_pick`), so it waits on him as
  long as the trade market would. A jump-in draft does the same for anyone a hard tag rules out of its first
  week, whom the current market's ADP leaves out altogether (a superflex jump-in used to leave QBs worth
  400-700 on IR on the wire). A week-1 O or Q is game-week news and
  appears when the draft ends, and the CPUs make their week-1 moves first, as they do every week.
- **Draft:** a snake draft, 15 rounds by default (QB, 2 RB, 2 WR, TE, FLEX, K, DST, 6 bench). CPUs follow
  ADP: each takes the lowest `(ADP + noise) x need x taste` among the best players left, with position
  caps, K and DST in the last rounds, and never reaches further than a round of picks past the current
  pick in rounds 1-4, a round and a half in rounds 5-9, and 25 picks after that (never more than 25).
  "Hard" halves the noise. The draft board (`GET .../draft?sort=rank|adp|projection&direction=`) defaults
  to **Rank** = `pre_rank` (below), with **Rd** = ceil(rank / teams); players without the number sorted on
  come last either way.
- **CPU rosters:** at most 2 QB, 2 TE, 1 K, 1 DST, 6 RB and 7 WR. A CPU takes one extra only while nobody
  it has at that starting slot plays that week, and trims it afterwards. A CPU never drops a top-100
  player (100 in a 12-team league, scaled to the league size: 66 with 8 teams, 116 with 14), nor in the
  league's first 4 weeks one of the league's picks from rounds 1-8 (`replay_waiver_ai.EARLY_ROUNDS` /
  `EARLY_WEEKS`: about three games before a manager gives up on a mid-round pick) unless the news has him out
  for the season. A player it added this week, one it is "high on" and one at a position it needs go only after
  every other player it can spare, and a better player who is only on a bye is never cut to cover a slot. After
  a trade forces a cut, the CPU refills the open roster spot. A stasher never offers away the injured stars he
  collects. It keeps an injured player who was above replacement, or who has a healthy chart of 50 or
  more (`KEEP_OUT_CHART`) and a return date in the news; when it trims back to a positional limit, players
  whose season values are within 5 points (`TRIM_CLOSE`, often all 0) are cut cheapest on the chart first. It
  drops an injured player it would otherwise keep only in one case: a starting slot has nobody who plays, its IR
  slot is taken, and the player is on IR or PUP and outside that top group; he then goes
  for a free agent who plays. It streams kickers and defenses: a unit that plays is swapped only for one
  projected `replay_waiver_ai.STREAM_EDGE` (4) points better; one on a bye or without a projection is
  always replaced. About 40% of CPU managers (`STREAMER_SHARE`, picked once per game from its seed) are
  streamers who chase the matchup: they swap for `STREAMER_EDGE` (1.5) points.
- **Values:** `ros_ppg` is the weighted mean (4, 3, 2, 1) of a player's last four pre-game projections,
  with a preseason rate (the ADP order mapped onto the week-1 projection curve) filling the slots that
  do not exist yet. Waivers still use points over replacement (`trade_value`). Trades and the Value
  column use a **0-1000 trade chart** (`services/replay_chart.py`): a player's rank at his position is
  turned into the ADP pick where that season's Nth player at the position went, and the pick into
  1000 x exp(-(pick - 1) / 40), so a star is worth more than two good players. Kickers and defenses are
  floored: the Nth K or DST (0-based) sits at pick 150 + N at the earliest (`KDST_FIRST_PICK`), because the
  ADP source drafts them far too early (the top DST around pick 95), which priced a DST1 like a WR4 and put
  streaming defenses at the top of the wire and in rounds 9-13 of the board. `pre_rank` (the board's
  Rank, and `star`) is the overall order of these preseason picks: the slot of his preseason expert rank,
  else of his ADP at his position. The rank blends this
  week's expert rank (weight 0.65 for a fresh snapshot, less as it ages) with the app's model rank
  (`ros_ppg` nudged by his last five real games, at weight 0.5: the user chose five over three so values swing a
  little less; after a big move with no news the old value was closer to the next four weeks 71% of the time,
  2020-25, and five games cut such moves by 17%). Two extras keep the model rank from trailing the news
  (`services/replay_role.py`): from week 2 his newest projection counts half its gap to `ros_ppg` (a back
  the site re-rates after he wins the job moves now, not a quarter at a time), and when a teammate at his
  position is expected to miss games, a player with no projection since then (out or on a bye himself,
  e.g. Mike Evans when Chris Godwin went down in 2024) gains his share of the missed work (75% of a back's
  stays with the backs, 60% for receivers, 50% for tight ends, split by rate). Judged out of sample
  (2021-2025, weeks 2-11, against real points from that week on): per-position rank correlation 0.475 ->
  0.478, better in 4 of 5 seasons. A usage-share signal (a back's share of his team's touches) was tried and
  left out: it moved players the wrong way more often than the right one. 2018 and 2019 have no expert
  ranks, and the model alone swung values hard (Deshaun Watson 2019: 124 -> 495 -> 633 in three weeks): there
  the ranks of `ros_ppg` (the sites' slow consensus) stand in for the experts at the same 0.65 weight.
  Median week-to-week move of a top-24 QB 0.40 -> 0.26 (log; 2018 0.53 -> 0.29), and Spearman against real
  points from then on 0.484 -> 0.495 (2018 0.500 -> 0.512); seasons with expert ranks are unchanged. Nothing
  goes above 1000, the bye bump and hype included; values show as whole numbers on every screen (lists,
  card, roster, trade builder, offers), rounded half up, so a player reads the same everywhere, in every
  season, 2027 included.
- **Superflex values** (`services/replay_superflex.py`): RBs, WRs, TEs, kickers and defenses are valued
  exactly as in a regular league of the same scoring (their curves, ranks and `star` come from the
  league-scoring 1-QB list; a non-QB's chart is identical with and without the SUPERFLEX slot). Each QB takes
  the value of the non-QBs drafted next to him in the real 2-QB list: count the non-QBs ahead of QB n there
  (fractionally, by how far his ADP went toward the next one) and give him the pick where that non-QB slot
  went on the league-scoring list (2026: QB12 goes at pick 23 with 11 non-QBs ahead, so he is worth what the
  11th-12th non-QB is). The CPU draft and the board's Rank place QBs the same way. A season without a 2-QB
  list uses the average slot of QB n over the 2018-2026 seasons that have both lists, from FFC's own rows
  only (`python -m services.replay_superflex --fit` -> `replay_superflex.json`; it refuses to write when a
  list is stored for no season); a league saved before `scoring_adp` existed keeps its 2-QB list's order for
  non-QBs, behind an average 1-QB list's QBs (frozen: today's database is never read for it), and so does a
  non-QB the scoring list lacks. Before this, a superflex league valued
  everyone on the 2-QB list's picks: with 13 QBs in the first 24 picks (2026) every non-QB slid down the
  curve (league "Super", week 8: Ja'Marr Chase 869 -> 709, Amon-Ra St. Brown 855 -> 567, Brock Bowers
  432 -> 175), and that list orders non-QBs like standard scoring, which sank tight ends in PPR. A new
  superflex league values them as its regular twin does; "Super" itself was saved before, so its non-QBs keep
  the 2-QB list's order on the 1-QB scale.
- **Injured players' charts** (`services/replay_chart_injury.py`, `replay_values._frozen`): while a player is
  out with an injury or suspension (from a hard tag until the first game he is available for: he plays, or his
  team plays and he carries no hard tag, so a healthy scratch, a backup without a stat line or a released
  player is valued live again from the week after), his healthy value is the one he had the week before the
  absence began (built from what was known that week, with today's bye bump like anyone's; a player hurt before
  week 1 keeps his draft-time value; one listed nowhere that week keeps his live value; a second absence right
  after a return freezes at his live value of the return week), and
  the chart is that value less a share that follows the user's targets: out 1-4 weeks about 5-12%, 6+ weeks
  35-45% when first reported and less each week as the return nears, out for the season (or a return after
  the league's last week) the whole chart. At 13 games left: 1 game missed 6%, 3 10%, 4 12%, 6 40%, 8 45%,
  12 45%; never less than (share of the remaining games missed)^1.8 (`SHARE_POWER`, the user's choice), which
  prices a player who misses most of what is left: 1 of the last 2 games 29%, 3 of 6 29%, 4 of 6 48%, 5 of 6
  72% (testers saw Kenneth Walker, back for the last 2 of 6, still RB7). More games missed never cost less,
  and a fixed return week never costs more the next week. So the value falls once when he goes out and climbs back each week, reaching
  his old value the week he returns, times a return factor after a long absence (`replay_chart_injury.return_factor`:
  1 for up to 4 games missed, 0.80 from 6, measured: players back from 6-9 games out scored 0.80 of their old
  rate against healthy players of the same form; Christian McCaffrey 2024 returns at 800, RB4, not 1000; after
  his first game back he is valued live): before this the experts' and the model's ranks fell while he was out and
  the discount came on top (2026 superflex QA league: Aaron Rodgers missing one game 474 -> 129, now 446;
  2027: Jonathan Taylor out weeks 7-14 789 -> 209, now 454 climbing to 741; real 2024: Puka Nacua worth least,
  214, the week he returned, now 714). The games missed come from the news report. An "out for the season"
  report may be wrong (Puka Nacua 2027, back in week 14), so it keeps what such reports were really worth over
  every IR or PUP absence 2018-25 (`python -m services.replay_chart_eval --season-reports`): 0.3% of the chart
  with 0-3 games left, 1.8% with 4-7, 6.5% with 8-11, 16.7% with 12+ (they came back 0.3%, 2.3%, 9.5% and 27%
  of the time). An out player without a report (no hard tag: his live value, not frozen, since that is how a
  lost role looks too) loses the expected discount over how long such absences really lasted (2018-25, 3,253
  player-weeks: 16% played that week after all, 23% missed one game, 13% two, 19% never came back that season;
  `--absences`), each outcome cut at the games left: about 29% at 13 games left, 43% at 4, 84% at 1, never all
  of it; tagged IR, at least 45%. Availability and expected games missed (lineups, waivers) stay the real
  expected games. Before this round the chart was healthy x share of games played x 0.9 (a star missing 3 of 13
  lost 31%). Measured honestly: real results say a known absence costs at least its share of games, and players
  come back worse than before it (out players produced 0.40 of their pre-injury value per game over
  replacement; the chart keeps 0.63), so the user's targets price injured players above what they realised
  (held-out error of out players 3.185 -> 3.516 with the frozen value, see below).
- **Fill-in quarterbacks' charts** (`services/replay_fillin.py`): a QB starting while his team's regular starter
  carries a hard tag (or the backup expected to start), one whose regular has just come back, or one who took the
  job, is priced at (share of his team's games left he is expected to start) x the value of his rank without the
  form bonus + (the rest) x a backup's value (the QB ranked just past one per team), never above his own chart.
  The regular starter is the team's first passing-yards leader, and he changes only when a healthy regular does not
  play (one definition, shared with the simulator's realism measure, `sim_qb_fillins`). The shares are fitted on
  every real fill-in situation of 2019-2025 by starts so far and what the regular's news says (the same noisy
  report his injured chart reads; `python -m services.replay_fillin --fit` -> `replay_fillin.json`): a named
  return -> the share such fill-ins really started (31-48%), never more than the games before the reported week
  plus the 17-38% they kept after it; out for the season -> 57-63%; the regular back -> 9-40%; the job taken ->
  47-72%. A fill-in becomes the team's QB after 5 consecutive starts (real fill-ins who kept the job had a median
  expert rank of QB23 after 5 starts, QB27 after 4; those who gave it back stayed at 27-29) unless his regular is
  expected back (a named return week): the return still bounds him. While the regular is out, his availability
  plus the fill-in's share averages 0.89 (2020-2025, 97 situations), and the fill-in's never passes 0.65, so the
  two are never both priced as the team's full-time quarterback. A 12-team superflex league, before -> after:
  Joe Flacco 2023 weeks 14-17 175-403 -> 98-142, Joshua Dobbs 2023 (Minnesota) 208-508 -> 118-204, Cooper
  Rush 2024 22-165 -> 22-79; Brock Purdy 2022 124-426 -> 72-176 while filling in, 339 at his fifth start; Jacoby Brissett
  2025 278-291 -> 112-123, then 518 at his fifth start with the job his. On the chart check below (common player-weeks):
  regular lineup unchanged (Spearman 0.553, held-out error 4.039; QBs 0.321 -> 0.322); superflex QBs rank better
  (0.591 -> 0.594) but the held-out error rises 5.038 -> 5.048, because superflex QBs are already priced under
  what they produce (QB bias +5.08 -> +5.13 points a game).
- **Chart check against real results** (`python -m services.replay_chart_eval [--seasons 2018-2025]
  [--weeks 1,4,7,10,13] [--lineups regular,superflex] [--teams 12] [--bench 6] [--out report.json]`;
  `--compare before.json after.json` scores two saved runs on their common player-weeks; `--absences`
  measures unreported absences): for each real season and week it builds the point-in-time values and scores
  each skill player's chart against his points over replacement per game from then on. A team game he missed
  counts 0 (a benching, a lost role, a healthy scratch: busts are what the chart must price) unless an
  injury or suspension that started after that week was open (a hard tag since his last game): those games
  are left out, because trade value cannot know an injury that has not happened yet. A bye is no game. For
  an absence known that week: his rate from his return on x the share of the remaining games that absence
  left him (back with fewer than 3 games: his games from before the absence count too; never back: 0).
  Replacement is the lineup's (teams, starters, FLEX, SUPERFLEX, bench). It reports Spearman correlation
  overall and by position, mean value per chart band and position, a leave-season-out calibration error (a
  position-blind band curve fitted on the other seasons) with each position's and position-rank tier's
  bias, the same for players out that week, and how many player-weeks each rule scored (12-team PPR,
  2018-2025, regular lineup, before this round's changes: 2,402 healthy; 1,385 healthy with a later injury
  left out; 643 healthy with missed games counted as 0; 369 out and back, 50 of them back late; 184 out for
  the rest of the season; 113 dropped for fewer than 3 games). Results (weeks 1/4/7/10/13, common
  player-weeks, before -> after): QB mapping and injury targets: regular lineup Spearman 0.561 -> 0.555,
  held-out error 4.008 -> 4.023, out players 3.006 -> 3.159; superflex 0.561 -> 0.553, 5.040 -> 5.046; over the
  last starter (`--bench 0`) 0.533 -> 0.532 and 4.118 -> 4.114. The frozen value of injured players: regular
  0.552 -> 0.538, 4.028 -> 4.072, out players 3.185 -> 3.516; superflex 0.575 -> 0.560, 5.007 -> 5.069, out
  players 3.732 -> 4.162; ending an absence at the first game he is available for, the continuous discount
  and the bye bump: regular out players 3.325 -> 3.305, superflex 4.048 -> 4.023. On the full population, which now keeps the non-QBs that used to slide below 50,
  superflex Spearman 0.561 -> 0.579 and held-out error 5.043 -> 4.998 for the QB mapping.
  What it found and left alone: superflex QBs at every tier produce more per game over a bench-deep
  replacement than equal-chart non-QBs (QB1-6 +4.8, 7-12 +5.3, 13-24 +5.7, 25-36 +3.3 points a game), and
  over the last starter QB1-6 +0.85, 7-12 +1.05, 13-24 +0.98, 25-36 -0.30, where running backs below the top
  six sit at -0.9 to -1.6: mid and lower QBs are not worth less than their non-QB neighbours, so no
  taper. Tight ends are near par in a regular lineup (bias -0.23) and the sign flips with the replacement
  definition in superflex (-1.3 with bench replacement, +1.3 over the last starter), so no fitted fix. CPUs
  in superflex leagues claim QBs only when they start that week (fill-ins projected 8-20), never a sitting
  backup, though the near-zero QB replacement level (0.6 points a game in a 14-team superflex league) ranks
  such fill-ins above most bench backs and receivers.
- **Market reaction (hype):** trade values model what managers would pay, not only the forecast, and CPUs
  price deals with the same values, so you can buy before or sell into a move. `services/replay_shocks.py`
  detects events the market reacts to on little or no game sample, from point-in-time data only: a teammate
  at his position ranked inside the top 36 RB / 48 WR / 18 TE goes out for at least a quarter of the games
  left (the injury news counts, "out for the season" fully) or leaves for another team (the next back or
  receiver in line gains); that teammate comes back after two or more weeks out with an injury tag (the
  stand-in loses; an untagged absence is a scratch, trade or release, e.g. Cam Akers' 2023 first game for
  Minnesota, which used to cut Kyren Williams); a
  player's week 1-2 share of his team's work is well above or below what his preseason rank implies (role
  won / lost). One game's points never move anything. Each event pulls his chart rank part of the way
  toward the rank it points to (the lost starter's, his old one, the one his share implies), fading over 4
  weeks and shrinking as the experts and projections catch up, so nothing counts twice. The size of each
  pull is calibrated (`python -m services.replay_shock_eval`, 2018-25, leave one season out) on how far the
  market really moved in the 1-3 weeks after: the expert ranks and the projections, minus the drift every
  other player saw (no Vegas player props exist for past seasons: the app holds props for 2026 only). A
  (kind, position) with fewer than 20 events uses its kind's pooled fit (smaller fits made role_lost TE and
  teammate_left TE worse out of sample). Honest result: the chart already moves at the event week about as
  far as that market later does (median gap about 0), so most pulls are small: the fill-in's drop when the
  starter returns is the largest (RB 0.48 of the gap), a back's gain from a lost starter 0.15, a teammate
  traded away 0.17, role won 0.02-0.05, role lost about 0. Javonte Williams in 2025 week 2 goes 166 -> 181
  (RB26 -> RB25; the experts reached RB16-20 only in weeks 3-5). Lead: mean distance to the market's later
  rank 11.3 -> 11.2 ranks. Forecast cost: mean Spearman against real points from then on moves -0.009 to
  +0.002 per season (2022 worst). A trade is seen only after the player's first game for his new team (no
  stored source is point-in-time before it), so a teammate_left pull comes a week late.
  A player a shock lifts by at least 1 chart point gets a **hype star** next to his name wherever player
  rows show, with the public reason ("Hype: role jumped after Puka Nacua went out for the season", "... he
  won the backfield").
- **Injury news:** a player who is out carries a report of when he is due back (`services/replay_news.py`).
  Until a seeded 1-7 weeks before his real return it reads "no timetable yet", and his value is the average over
  what the public can know (how much longer real 2018-25 absences with that tag lasted once he had been out this
  long, `replay_news_lengths.json`, fitted by `python -m services.replay_chart_eval --news-lengths`): the expected
  return factor x share kept, the same for every league seed and smooth week to week, never from his return (the
  report's own week, for lineups, is that spread's median); from then on it names his real week plus an error drawn afresh each week (one week out it is right
  about four times in five; 2 weeks out 59%, 3 weeks 41%, 4+ 37%), and a few long in-season IR absences first
  read "out for the season". A preseason IR or PUP move reads "no timetable yet" at the draft (valued on the
  preseason prior), a suspension shows its length. It never names a week already reached, a returning player's
  week stays inside the season, and a plain O tag never reads "out for the season". Old saves keep loading; their
  past weeks' news text (and the values built on it) is recomputed with these rules. Only an IR or PUP tag is ever
  called "out for the season": a plain O tag is week-to-week news (Cam Newton 2019: O from week 3, IR in
  week 10), so it names a return, the next week once the first guess has passed, until he goes to IR. A
  suspension is reported exactly. The commissioner's exempt list reads "out indefinitely" and never names a
  week; the values guess one (4-8 weeks after he went on the list, then the next week) without the real return. This is the one sanctioned use of
  later data (a beat report would know as much). Shown under a tagged player on the Players and Trades
  screens.
- **The week:** Wednesday waivers by rolling priority (CPUs claim when a player adds about a lineup point
  a week), then free agency, then the games. In free agency a CPU also adds a free agent whose trade value
  clearly beats a player it can drop at a position it can use (1.5 x his value and 100 more,
  `replay_waiver_ai._upgrade`), straight to IR when he is out, and every CPU keeps its most valuable eligible
  player in its IR slot, so a valuable player rarely sits on the wire: every team's best lineup by projection (yours can be set
  by hand), scored with real points. With waivers off, every unrostered player is a free agent.
- **FAAB:** the same week, but each claim carries a blind bid from a $100 season budget (whole dollars, $0
  allowed). At the waiver run the highest bid wins, ties go to the team earlier in the rolling waiver order,
  then to the claim higher in that team's list; the winner pays and moves to the back of the order. A claim
  whose bid is more than the budget left when its turn comes fails. Winning bids are public in the activity
  log ("claimed X for $12"); pending bids are never shown, budgets left are. Free-agent adds cost nothing.
  You can change a pending claim's bid until the run. CPUs bid a share of what they have left that grows
  with the claim's lineup gain (`replay_claim_ai.BID_PER_POINT`: 8% of the budget left per lineup point a
  week, more once fewer than 8 runs remain), never more than 40% of what is left until the last 4 waiver
  runs, with seeded noise of up to 20% either way; each of a CPU's claims is bid against the same budget.
- **Breakout claims:** at a waiver run (Wednesday or FAAB; free agency and "none" leagues are unchanged), a
  CPU also looks at "hot" players: a skill player not out, with at least two real games, who played in the
  last two weeks and whose mean over his last two games is at least 6 points over his `ros_ppg` (one big
  game is not a streak). It values him at a hot rate (half that
  recent mean, half `ros_ppg`) for the weeks after this one and claims him with half the usual bar (half a
  lineup point a week), so a breakout is claimed soon after his big game rather than once his projections
  catch up.
- **Usage:** the Players screen shows snap %, targets, target share and carries, averaged over his last 3
  games before this week. A skill player's card has two tabs: **Game log** and **Advanced** (season
  averages of snap %, target share, targets and carries a game, catch %, yards per target and per carry;
  snap % and target share over his last 3 games against the season as Up / Down / Steady; and a weekly
  table of the same, computed from each played week's usage and box score). Only
  weeks already played; snap % only where snap counts are loaded; no red-zone numbers (not in the data).
- **Trades:** a CPU scores a deal on the chart (`services/replay_trade_value.py`): what it receives and
  keeps, minus its forced drops, plus a premium for getting the best player in the deal, a little for
  roster spots, half its lineup gain or its whole lineup loss, against what it sends plus a 3% margin (and
  8% more from you on hard). Up to 6 players a side, but depth is worth less than quality: when a CPU
  keeps more players than it sends pieces that matter (a junk throw-in asked of it doesn't count), its
  smallest pieces past the first extra one count at
  `replay_trade_value.DEPTH_SHARE` (35%), so five bench players worth twice a starter on the chart still
  don't buy him, while a fair 2-for-1 is unchanged. It also refuses a deal
  that loses it 20% of market value (counted with the same depth discount). Declines come with the reasons.
  The verdict's headline ("Slight loss for you") is on trade value; its totals add the best-player premium
  and take off your drops (it says so when they differ from the rows), and a lineup loss of 10 or more
  points over the rest of the season gets its own warning under the lineup line. A CPU's lineup loss counts
  in full but at most 15% of what it sends (`LINEUP_CAP`), so a CPU can accept a deal that costs its lineup
  when the value is right (selling its QB in a one-QB league), as real managers do. A player on IR can be traded: he takes the receiving team's free IR slot, else a
  roster spot (which may force a drop). A CPU
  sells you an out player only for at least half his healthy chart value. A player his team added or claimed
  this week and worth under 100 on the chart counts half (`replay_trade_value.PICKUP_SHARE`) in every trade
  price until the next week, for the CPU and in your verdict alike (the row says "Just picked up"), so a
  pickup can't be flipped for value the week he arrives. A CPU's positional limits hold in trades too: a deal
  that takes it past one cuts its cheapest player there as part of the deal, counted in its value test like
  any cut of its own. A done deal or a drop expires every other pending offer naming a player who moved.
  When a CPU sends you an offer because it wants one of your players, it gives you 5-10% more trade value
  than it gets, by persona (`replay_personas.OFFER_EDGE`: steady 5-6%, shark and dealer 5-7%, balanced, fan, stasher,
  contender and a calm tilted manager 6-8%, panicky and gambler 8-10%; the haggler's opening offer is the
  exception, see below), judged as its own offer (`replay_trades.offer_multiple`) both when made and when you accept
  it; a shop's offer is priced as before, at the shop discount. How a CPU's own offer leans is drawn per offer
  (`replay_personas.favours`, the user's "about 50/50"): with FAVOUR_SHARE (panicky, gambler and tilted 65%,
  shark 40%, everyone else 55%) it favours you on the numbers the preview shows (a want offer by the edge
  above, a shop offer by 0-10%, judged at the same own-offer bar accepting it uses), otherwise it comes close
  (up to 4% short); "a good deal, I promise" lines only go with an offer that favours you. Measured over 3
  seasons x 6 seeds (135 non-haggler offers): 51% favour you (shops 56%, want offers 45%) and 98% of all offers
  are within 5% (before this round: 35%; every want offer favoured you, shop offers rarely did). After a deal each starting slot
  the CPU can no longer fill needs a player it can add (unrostered, not out, not on waivers; in the waiver
  phase, when everyone unrostered is on waivers, anyone you haven't claimed), and it adds him
  (`replay_cover.cpu_cover`): right away, or in the waiver phase once waivers clear. Each CPU has a hidden **persona**
  (`services/replay_personas.py`, assigned from the league's seed at creation: at least one shark and one
  panicky, no style on more than two CPUs; old saves keep theirs) that nudges what it thinks a player is worth
  and how often it offers and counters; after 3 weeks a one-line tendency hint is shown, never the persona:
  - balanced: the chart. panicky: marks a slumping player down (sells low). steady: won't sell a slumping star
    cheap. gambler: chases hot streaks and pays most for the best player in a deal. shark: wants a 5% edge and
    hunts buy-low stars (these nudges stay within 15% of the chart).
  - **fan**: a seeded favourite NFL team (`Team.fan_of`, `services/replay_fans.py`); its players count 10% more
    to him, his own and the ones he buys. He never takes a player of its rival (a fixed table: CHI-GB, DAL-PHI,
    KC-LV, ...; OAK counts as LV): he turns such a trade down, never offers one, never drafts, claims or adds
    one, unless nobody else can fill a starting slot. His tendency names both: "Bears fan. Wants no Packers."
  - **stasher**: takes off only half the market's injury discount for an out player with a return date, so he
    pays the most for hurt stars and looks at yours first.
  - **haggler**: his opening offer to you gives 10-15% *less* than you send and says it is an opening offer;
    decline it and he always comes back with a counter at 5-7% more for you (for the player he wanted most).
  - **contender**: while he holds a playoff spot (from league week 4), anyone who would start for him this week
    counts 9% more and his bench and out players 10% less; otherwise balanced.
  - **dealer**: offers you a deal about every other week (50%), counters 90% of the time, looks at 2-for-2
    swaps first and makes an extra CPU-CPU attempt half the weeks.
  - **tilted**: balanced until he loses two league games in a row; then he prices like panicky, offers more
    often (25%) and his offers give you more with each loss (2: 8-10%, 3: 10-13%, 4+: 12-15%).
  **High on a player** (his mood line says "High on X"): he asks 8% more for him at the threshold, up to 15% for
  a strong feeling, from anyone, CPUs included; worried caps his price at the chart, so a mood and a price never
  point opposite ways. **Needs:** a CPU needs a position when its weakest starter there is under half the
  league's median weakest starter (or it has too few), and is deep at one when it has a starter-quality player
  (at least that median) more than its starting slots there (a superflex slot counts as a QB slot; a flex slot
  belongs to no one position) (`replay_styles.position_needs`); the trade builder shows "Needs RB · Deep at
  WR" under the mood. It pays 5% more on its net gain at a position it needs: what it gets there that would
  start for it after the deal, less what it sends at that position (so swapping one back for a lesser one
  earns nothing); 10% when it pays only from positions where it is deep (a team short at RB with many good WRs
  gives a 5% better WR for a starting back); that and its lineup gain together add at most 10% of what it
  gives. Chaining such deals barely pays: in the test league a greedy user gains 1.5% of his roster's chart value
  in a week and 3.8% over four (1.3% without the bonus), because the re-trade lock keeps a player he just got for
  two weeks. Every style stays inside the market guard (it never takes back under 80% of what it gives on the
  plain chart), and a test trades round trips (one or two players each way) against every style: none leaves
  the user more than 2 points ahead of the best measured (all at or below even except the stasher, +1.2%).
  Leagues saved before these styles keep their old personas, but the need bonus and the "high on" mark-up apply
  to their CPUs too. A haggler's "opening offer" line and a fan's refusal of a rival's player can give the style
  away before the week-3 tendency hint does (intended). A fan never shops his own team's players, nor sends a
  worried line about one; a stasher's worried line is about a cold streak (he only buys and holds players coming
  back from injury). Panicky and gambler CPUs are also **emotional**: they price a
  hype move at 1.25x its calibrated size (`replay_personas.HYPE`, at most 8 chart points more), both when
  buying and when holding; the rest stay calm. Small on purpose: Javonte Williams' week-2 jump of 15 points
  becomes about 19 to an emotional CPU. **Counters:** a CPU that turns down a deal on value alone may
  answer with a counter offer it would take (`services/replay_counter.py`; one roll and at most one counter
  per CPU per week). A CPU that says no tells you why in one line in its voice, under its name in the trade
  window and in the trade history (`Offer.reply`). A league rule comes first, in his voice: a player in the deal
  was traded in the last 2 weeks, he has made his 3 trades this week, he has to sort out his IR first, or the deal
  would leave you a starting slot nobody can fill. Then: your player is a rival's (a fan), no roster or lineup
  room, it is high on the player you asked for and would have taken the deal without that mark-up, it loses
  lineup value that sank the deal (without the loss it would take it, or the loss is bigger than the value gap
  left; the "Value is too far apart" sentence then leads with that loss), what it gets would only fill positions
  where it is deep, or value: "close" when it needed at most 15% more than you offered, a lowball beyond that.
  The first that is true, each tested by taking that one term away (`services/replay_trade_reasons.py`); a
  declined proposal always gets a reply; shown under Offers for you (an offer lapses when the week is played: the hub warns
  while one is waiting);
  you can counter a CPU's offer from the trade builder (the offer is marked "countered"). The **deadline**:
  trades are open through week 11 and its waiver phase, and close when you press Sim to Wednesday after
  week 11's games (in a league without waivers, once week 11 is played). CPUs also trade with each other
  and send you offers. A CPU makes at most 3 trades a week (`replay_state.MAX_TRADES_PER_WEEK`, either
  side; a fourth is declined); you have no cap.
- **Moods and shopping** (`services/replay_mood.py`): every CPU has feelings about his own healthy skill
  players, from the same numbers his price uses (recent games `form_chart` plus hype x `shock_chart`), so what
  he says never contradicts what he accepts. Panicky managers feel a slump 1.8x, gamblers a hot streak 1.8x,
  steady ones a slump only 0.3x (and stay high on a slumping star, cooler on one who outran draft day).
  Strength is that signal over his chart (at least 250): worried at -0.08 or below, high at +0.08 or above,
  and only on the side of the chart his own price is on (worried only when he values the player at or below
  the chart, high only at or above it), so a mood line or message never points the other way from his price.
  Worry also needs his last two games below his rate (never one bad game, never a player scoring at or above
  it), and nobody is worried about a player he got this week. Once 3 NFL weeks of results are known (week 4 on; a league
  that jumped in at week 4 or later has them from its first week, since the real weeks before it are known) each CPU shows a **mood
  line** in the trade builder ("Worried about X. High on Y."). When a CPU is worried at -0.15 or worse about
  one of his 6 most valuable skill players whose last two games fell short of his rate (and who nobody could think
  was hurt: the same test as the chat's dud lines below), he may **shop him to you** at the start of a week,
  after the CPUs' IR and free-agency moves
  (chance by persona: panicky 75%, gambler 35%, balanced 25%, steady and shark never; one shop per CPU, at
  most 2 in the league): he sends you a message, the player shows under **Shopping to you** on the Trades
  tab, and for you only he asks a seeded 5-15% less for that player (panicky 9%, gambler 7%, balanced 6%,
  plus more the more worried he is). His own shop offer is judged at the bar he uses for his own offers, so the
  discount stacks with his persona's offer edge: a shopped player can go for about 0.79 of his price, not only
  5-15% off (still inside the 80% market guard). The discount is applied once, in the CPU's value test
  (`replay_trade_value.tally` via `replay_state.markdowns`), so evaluate, propose, counters and his own offer
  all see it; it never moves the chart, and the re-trade lock stops you flipping the player. A manager who
  shops a starter has chosen to lose him, so a deal for him carries no lineup-loss cost for the CPU: an
  equal-value one-for-one at a position he can use goes through. While the shop is
  open the player is yours to buy: no other CPU can trade for him, and the shopping CPU sends you his offer
  first, once per shop (decline it or let it expire and he doesn't send it again while that shop stays open,
  though you can still propose at the discount; other CPUs' offers come through as usual; if the shop closes
  and opens again later, he sends a new one). The shop stays open week to week while he stays
  that worried; it closes after a good game, if the player might be hurt, once the trade deadline week is
  over (no shop opens after it), or as soon as the player leaves his active roster (a trade, a drop or IR).
  In a league with waivers a shop opens at Sim to Wednesday, so last week's shops wait out the waiver phase
  without their discount until then. No shop opens for a player who is on IR or was traded in the last 2 weeks.
- **League chat** (`services/replay_chat.py`, lines in `services/replay_chat_lines.json`): a one-way group
  chat under League > Chat (an unread dot on the League tab). After each week every CPU may say one thing:
  a championship or final loss, clinching or elimination, an upset (beating a team 4+ places above him, from
  the league's 4th week), a
  3+ game streak, a starter's huge game (22% of the league's median team score and 1.75x his rate) or dud
  (40% of a 10+ rate; never a player who scored 0 or might be hurt: a tag that week or the next, no
  projection next week, a snap share under 60% of his season median, or no snap counts to tell (a bye
  is not a maybe: he had no game to get hurt in, unless he carries a tag); so week 1
  has no dud lines, with no earlier snap share to compare against), or the
  result itself (close under 9% of the median team score, a blowout from 22%: about 10 and 25 points in a
  half-PPR league, more in a high-scoring one). The final's champion and runner-up don't talk margins after
  a tied final. Your opponent talks to you (and also posts his clinching or elimination when that game
  settles it); the others post now and then (a boom or dud half the time, a plain result 30%), at most 5 of these after-the-games lines a week: your
  opponent's line to you is always kept, then the rare news (title, clinching, elimination), then the rest in a seeded order,
  and a line that would run past 140 characters with long names gives way to one that fits. A third manager may react to a
  trade (at most 2 a week) and a shopping CPU messages you (at most 2 shops are open at once); these come on
  top of the 5, so a week can show up to 9 lines. Every CPU offer carries a one-line reason in
  his voice (worried about a player, never one who might be hurt; a counter; a haggler's opening offer; high
  on yours; buying low on a slumping or hurt player of yours; short at a position he needs; or a value pitch).
  In a league that started mid-season (or a simulated one) the chat counts the league's own weeks (moods and
  shops only need 3 NFL weeks of results, see above), and each line is dated with the real NFL week.
  Lines come from a bank of 2,300+ clean lines in eleven voices, one per persona, so a manager sounds the same
  all season; a game never repeats a line until that bucket is used up (except when every unused line runs
  past 140 characters once the names are in: then the next one that fits is reused), and the text is copied
  into the save.
- **Season:** the user picks the championship week (16, 17 or 18; 18 only when the season has a week 18)
  and the playoff teams P (4-8, at most the league size; default 4 under 10 teams, 6 under 14, else 8).
  The playoffs last 2 weeks for P = 4 and 3 weeks for 5-8, and the regular season is the weeks before
  them (e.g. a week-17 final: 15 weeks with 4 teams, 14 with 6). The title bracket is reseeded every
  round (the top seed left plays the lowest); with 5-7 teams the top 8 - P seeds get a round-one bye. The
  semi-final losers play for third in the final week. Round-one losers (P >= 5) play on for places 5..P,
  and the teams that missed the playoffs for P+1..N: each week a pool ranks its teams by pool wins, then
  points in pool weeks, then seed, and pairs them off (1 v 2, 3 v 4); in an odd pool the team idle least
  so far sits out (then the lowest in the table), so the bye rotates, and a pool
  of two plays twice (the team that won both places higher; a split goes to more points, then the higher
  seed). A pool's final places are its table after its last week, not head-to-head: a team that won one
  consolation game can finish behind the team it beat if it lost its others (the Playoffs page says so).
  Games are labelled Quarter-final, Semi-final, Championship, Third place or Consolation; a bye
  shows as an idle week on the schedule ("Bye / no game"), and the Matchup tab then shows the week's
  first game. With 5 playoff teams round one is a single wild-card game. Leagues saved before playoff
  sizes existed keep their title games. Their consolation games may pair differently (by the pool table rather than
  seeds 5 v 8, 6 v 7): an 8-team league saved before its first playoff week, and a league of 10 or more
  teams saved before its last playoff week. A finished one may reorder places from 5th down.
  The League tab's **Playoffs** page (`GET /api/replay/games/{id}/playoffs`; the League tab opens on it once
  the regular season is over) draws this as a bracket: one column per playoff week with every slot, seed
  numbers, byes, scores, the winner of each game marked and the Championship on the bracket's midline, the
  user's games outlined; slots not decided yet say "To be decided". Until the seeds lock, round one is drawn
  from the current standings and marked Projected. Below it, each consolation pool (the places it decides,
  its teams, or who will join it) with its games week by week; a week not paired yet says when it will be.
  On a phone the columns stack. A game whose teams are set and whose week has come is a link to that game
  on the Matchup tab (`#/play?game=ID&view=matchup&week=W&team=T`; the current week's opens its preview);
  later weeks and a projected round one aren't links. The **Standings** table sorts by PF or PA (click the
  header, again to flip); Seed (or Finish) goes back to the standings order, the only order with the cut line.
- **Limits of "no hindsight":** a week's projection is the sites' final pre-game number, and it is
  already visible at that week's Wednesday waiver run. An expert snapshot taken on a Friday already knows
  that week's Thursday game. Injury news is built from the real return week (noisy, see above). A player appears on the board and the wire once
  he has an ADP, a projection or a game by that week. A player's NFL team before his first game is the
  team of that first game.

The game is saved as one JSON document per league (`replay_leagues`); all randomness derives from the
league's seed, so reloading never re-rolls a pick or a trade. Response shapes are documented at the top
of `services/replay_views.py` and mirrored in `frontend/src/api/types/replay.ts` and `replayLeague.ts`.

The page is `frontend/src/pages/replay` (`#/replay` for the saved leagues and the new-league form,
inside the dashboard; `#/play?game=ID&view=team|matchup|players|trades|league` for a league, full screen
without the dashboard's bar or menu: an old `#/replay?game=` link is sent there). The game screen
(`layout/GameShell.tsx` + `GameHub`) has a sticky scorebug (**Exit** back to `#/replay`, the league, the
week, your score against your opponent, your record, a theme toggle and the one primary button; the
score is the current week's projection, so once a week is played it shows the next week's, and the final
is on the Matchup tab, until the season ends) and the five tabs (a bottom dock on phones). `GameHub` holds the game
summary; every change goes through its `act`, which swaps in the new summary, reloads the open screen
and lists what the action caused under "What just happened". The screens show only what the API sends,
and the API sends no result of an unplayed week. The look ("Game Night", `styles/game.css`, scoped to
`.game`) is a stadium scoreboard: dark is a night game, light a day game; each fantasy team has a colour
(`[data-team]`), never the only sign of who is who.
- **Lineups:** on My Team, drag a player onto another spot (HTML5 drag and drop, desktop) or use **Move**
  then a highlighted spot (touch and keyboard); both send the same lineup. Spots he can't go to are dimmed
  while a move is in progress; Escape cancels. A played week lists its bench with real points and a
  **Bench total** (weeks played before benches were saved say so instead).
- **Matchup:** a hero band with both teams and their scores (the biggest numbers on screen), then both
  lineups with their benches. Playing the week you are looking at keeps it on screen, after each week played
  in a row (a week you picked yourself stays put): the final counts up (at once with reduced motion) and
  is announced once to screen readers.
- **Players:** pick the stat columns with **Stats**: Season (points, PPG, games, rest-of-season rate and
  box-score totals), Last 4 avg (points and box score a game over his last 4 games, and snap share over
  his last 3) or Week N (that week's pre-game projection and, once played, his points and box line). The
  box-score columns follow the position tab (QB passing: completions, attempts, yards, TDs, INTs; RB carries
  and catches; WR/TE targets and receiving; none for K/DST). Each row also carries advanced rates over the same span
  (`services/replay_advanced.py`): snap % and target share (means over his games), targets, carries and
  touches a game, yards per carry / catch / target, catch rate, total TDs, TD rate per touch, passing
  yards a game, completion % and TD:INT, each blank when there is nothing to divide by. Click a column header to sort
  on the server (again to flip); players without the number come last either way.
- **Player card:** his status, five stat tiles, a game log of played weeks (opponent, projection, points,
  box line; a played week's opponent opens that NFL game's **box score**: the final score, then one tab per
  team with every pool player's passing (C/ATT first, "22/34", as on a real box score), rushing and receiving
  line and fantasy points under the league's scoring, K and DST points included; Back returns to the card).
  Completions and attempts come from nflverse in a real season and from the game draw in a simulated one; a
  simulated league saved before they were kept shows a dash for C/ATT (its passing lines have yards but no
  attempts, and the API sends null for both). The card's Advanced tab shows a QB's C/ATT a game and his
  season completion %. The card ends with the weeks to come: opponent (@ when away), a **matchup chip** and an **Est.**, his
  rest-of-season rate x the opponent's defense factor (not a site projection; none on a bye or while the
  injury news has him out).
- **Matchup ratings** (`services/replay_defense.py`): how many points each NFL defense gives up to a
  position, solved from this season's played games only (`schedule_strength._solve`: opponent-adjusted,
  shrunk toward average by games played), so a week's chip uses only the weeks before it. Last season's
  factors, when that season is loaded, are blended in at half strength and fade as games arrive
  (`factor = solved + (prior - 1) x 0.5 x 4 / (games + 4)`). Rank 1 gives up the most; the top quarter is
  **Easy**, the bottom quarter **Tough**. No chip for K/DST, a bye, or week 1 without last season. A played
  week's opponent in the game log is his real team's that week (a player traded mid-season shows his new
  team's game).
- **Standings** show each CPU's tendency under its name (never the hidden persona itself); team refs also
  carry the CPU's current **mood** line.
- New modules: `replay_box.py` (a game's box line, totals and averages), `replay_defense.py` and
  `replay_views_players.py` (the players list and player card; `replay_views_league` re-exports them).
  Bad `sort`, `dir`, `stats` or `stat_week` values on the players endpoint answer 422.

### Simulated seasons (Replay League)
Besides the finished seasons (2018 through the newest complete one: 2025 today), a Replay League can be
played on two seasons that have not happened yet, or not all of it: the **live** season (the one in
progress, 2026) and the **future** one (2027). Under **New league**, **What to play** lists **Past season** and each simulated season; a simulated
season that can't be played yet is greyed out with the reason. The rules of the game (draft, waivers,
trades, IR, playoffs, no hindsight) are the same as in a past season. A new league's default name is
"&lt;season&gt; Sim", and building its season takes about 10 seconds.

- **The live season:** real results through week N, the last week in which every game is final, the
  app's player stats are loaded and every scored game has both teams' team stats (`services/replay_live.py`; the stats come from the app's normal
  stats load, not from Prepare); weeks N+1 to 18 are simulated. It must be prepared like a past season, and
  prepared again each time a week finishes: until weeks 1 to N all have their projections, the season is
  not ready and a **Prepare 2026 data** button shows under the new-league form, or run
  `python -m services.replay_data --seasons 2026`. A re-run only fetches the new weeks' projections, reloads
  the injury tags and expert ranks when N has grown, and never re-downloads the game results. Fantasy
  Football Calculator has no ADP for a season in progress, so its preseason ADP is MyFantasyLeague's PPR
  list alone (real 12-team drafts from August 15 on; `services/replay_live_data.py`), which the other
  formats use as their nearest list until FFC has one.
- **A game without a score** is only treated as not played (a cancelled game, like Bills-Bengals in 2022)
  once a later game of the same season, at least 4 days after it, has its score. Its week then counts as
  final without it, so the season keeps moving and can end. A later score proves the schedule was read again
  well after the game, so data that simply hasn't arrived (a failed download, the app idle since before the
  game) never reads as a cancelled game, and a new league never freezes a team with a missing week. A
  cancelled game in the last regular-season week holds the season back until a playoff game has its score
  (about a week later).
- **Seasons from a bad feed:** a season later than the current calendar year is ignored.
- **When the season ends** (every week final), nothing needs changing: it becomes an ordinary past season.
  Prepare it once more (the button, or `--seasons 2026`) to fetch the last weeks' projections, tags and
  expert ranks; it then shows in the past-season list, can be drawn by **Random**, and next season's
  simulation is built from all of it. Next season stays the **future** season while only its schedule (or
  some unfinished week-1 stats) is loaded: it is simulated whole, on its real schedule once that is in the
  database. Once its week 1 is final it becomes the **live** season and the season after it the future one
  (which needs its own `backend/sim_data/offseason_<season>.json`; the seasons list says so until it
  exists).
- **Two ways to start a live league** (**Start**): **Full season** drafts in week 0 on the preseason ADP
  and plays weeks 1 to N with the real results, then the simulated rest. **Jump in now** drafts today and
  plays from week N+1: the draft knows everything real through week N (results, tags, projections and
  expert ranks); week N+1's injury report and expert ranks are simulated from that, like every later week's
  (the real ones, published during the week, are not read; the Jump in now choice says so), and the ADP is today's market (what a draft held under the
  current public view would look like, below), not August's. Its defaults: 4 playoff teams (the smallest
  bracket, so the longest regular season), the trade deadline two thirds of the way through the weeks
  left, and at least 3 regular-season weeks must be left (otherwise pick a later championship week or
  fewer playoff teams). The form shows the plan ("plays weeks 12-15 (4 weeks), then the playoffs in weeks
  16-17. Trade deadline: week 13."). Weeks before N+1 are history, not league weeks: the schedule,
  scoreboard, standings and week pickers start at N+1, while a player's game log still shows his real
  earlier games. A CPU's tendency hint shows after 3 league weeks; moods and shops start in the league's
  first week, from the real weeks already played.
- **The future season** (2027) is simulated whole, starting from what is known today: the public view of
  every player at the end of the newest season so far (2026 through its last finished week, so it moves as
  2026 is played), carried through an offseason (below). It needs that season prepared first.
- **Every league is frozen:** each new simulated league rolls its own rest of the season from a
  server-side seed (never the `seed` sent to create it, so a league can't be recreated to scout a future
  already played), so two leagues on 2026 play out differently, and the whole season (the real weeks too) is saved with the
  league (table `replay_season_data`, `services/replay_blob.py`, compressed versioned JSON). New real data
  (a finished week, an nflverse stat correction, an updated offseason file) reaches every league created
  after it, with no restart or Prepare needed (the season cache is keyed on the data), and never one created
  before it; deleting a league deletes its season. A league whose saved season can't be read (a damaged
  database file, or saved by a newer version of the app) answers with an error saying so instead of its
  screens (the server log records why, never the save's contents); it is still listed and can be deleted.
  If building a new league's season fails unexpectedly, the create answers 500 with the error's type only
  (the full trace is in the server log) and nothing is saved: never a league without its season.
  A saved season is decoded once per server run (about 1.5 s, ~9 MB in memory) and kept for the next
  12 simulated leagues opened (`replay_blob.CACHED`, was 4): the first screen of a league after a restart
  takes ~1.7 s, the next ~0.35 s, and switching between up to 12 simulated leagues never decodes again.
  Simulated games keep each team's final score (`SeasonData.scores`, shown on box scores once played);
  a league saved before that loads with no scores.
- **How the UI marks it:** the saved-leagues list puts a **Live 2026** or **Simulated** chip after the
  name; the game screen shows "2026 · simulated from week 12" or "2027 · simulated season" under the
  scorebug; on a player's card every simulated week in the game log and the weeks ahead carries a small
  **Sim** mark, with a line saying from which week the season is simulated.

**How a week is simulated** (`services/sim_*.py`). The simulator keeps two pictures of the league: the
**truth** (how good every player and team really is this season) and the **public belief** (what the
games so far say). Only the games (and the buzz, below) read the truth; projections, expert ranks, ADP and every CPU decision
read the belief, so, as in a real season, the public learns the truth only from results.

- **Every game has a pregame line** (`services/sim_lines.py`): an expected margin and total, built only
  from what is public before the week. Its inputs: each team's offense and defense rating from the points it
  scored and allowed so far (opponent-adjusted, recent games counting more, home field removed, last
  season's final rating carried in at 70% and fading as games come in), the belief's own expected points
  for each side with the players ruled out removed, each side's starting quarterback (the week's job holder, below, or
  whoever fills in for him when he is ruled out: his quality, and how far it is from the passer the team's
  numbers were earned with) and rest (coming off a bye, or a short
  week). How much each counts is fitted to the Vegas closing lines of 2018-2025, one season held out at a
  time (`python -m services.sim_lines_fit --fit`, coefficients in `sim_lines.json`), and judged on the real
  results of the held-out seasons: 2023-2025 weeks 5-18, the line's margin correlates 0.43 with the real
  margin (Vegas 0.51; the old belief alone 0.38) and its total 0.23 with the real total (Vegas 0.31; old
  0.21); its margins spread like Vegas's (sd 6.0 against 6.1) with the same home edge (1.6 points). Tried
  and dropped because they added nothing on held-out seasons: yards per play, team EPA (simulated games
  have none anyway), a dome flag, capping blowouts. In the live season the real line already posted for
  a game of the first simulated week (2026: week 12) is used instead (`sim_setup.posted_lines`), the spread
  and the total each where there is one (an out-of-range value is logged and ignored); a later week's posted
  line (week 13's) is not, since it knows nothing of what happened in the simulated week before it. Each
  simulated game's line is stored as that game's spread and total, as a real season's closing line is, and
  what a week shows (`as_of`) never includes a later week's line, real or simulated.
- **The result is drawn around the line:** the truth's expected points for each side are the line's plus
  what the public doesn't know (the truth's own expected points minus the belief's: hidden talent, a starter
  hurt during the game, a breakout the public has not seen yet; a quarterback benching is announced, so the
  line already has his replacement), plus each team's hidden strength (offense and defense, a few points each,
  drawn once a season; the public learns it from the scores), with the line's edge widened a little (real
  margins run about 1.1 x the Vegas spread). Each side's touchdown rates are then scaled by one factor
  (its yards by a power of it, and, for a side below its own expectation, its field-goal tries too, so even a
  6-point target is reachable) so its expected points hit that number, overtime included
  (`services/sim_centre.py`), and the game is drawn as below; the draw averages that number (within about 0.3
  points over 4,000 draws) and the week's projections are centred on the same line, so they stay unbiased. A team's touchdowns are drive-limited (their total is a
  binomial over a handful of "drives" around its expected touchdowns, then shared out play by play), because
  independent plays made scores too spread out once team strength was right. A game tied after regulation
  goes to **overtime**: one side wins it with a field goal or a touchdown (credited to a receiver or a
  rusher, never one who left the game hurt, so the box score still adds up); the share of overtimes that end
  tied (6.8%: all 8 real ties), that the winner ends with a field goal (57%) and the winning TD play's yards are measured on
  2018-2025 play-by-play (118 overtimes). **The last ten minutes are played from the score**
  (`services/sim_closing.py`): the game is drawn up to ten minutes before the end of regulation, then a real
  game's ending is drawn from the real games (2018-2025 play-by-play, `sim_closing_fit`, cached in
  `backend/data/cache/scoring_plays.json`) that stood at the same margin then, the trailing team's scoring going
  to the trailing side and the leader's to the leader. So a tied or close game is decided by a field goal as
  often as a real one, a team down 4-8 chases a touchdown and a big lead is run out: real margins land on exactly
  3 or 7 far more often than two teams scoring on their own would (pairing each real team's score with another
  game's opponent gives margins of 3 in 6.1% of games, real 14.4%). The ending's points go to real players (a
  touchdown catch to a receiver, with the passer's line still his receivers' sum, a run to a rusher, field goals
  and extra points to the kicker, return TDs and safeties to the defense), and the draw before the cut is
  centred without the window's and overtime's mean points (`services/sim_closing_means.py`), worked out from the
  game's own expected lead and total: the margin at the cut spreads more in a high-scoring game (sd^2 = 20.1 x
  (the two sides' expected points before the cut)^0.48, `cut_var`, measured by the fit: 8.4 at 14 points, 11.7 at
  56; a straight line in the total put low-scoring games' spread 0.4 too wide once the game was drawn in halves), and over that
  spread the real endings give each side's mean window points (a trailer scores more in it, and the closer the
  cut the more both kick) and the chance regulation ends tied, which sets each side's overtime points. So
  projections stay unbiased: over 12,000 draws a game averages its target within 0.13 points (se 0.07-0.09) at
  15-13, 20.5-20.5, 27-17 and 31-26 (one spread and one tie rate for every game had a 17-17 game at +0.23 / +0.25
  and a 10-13 one at +0.38); its players' projections are their draws' means too (quarterbacks within 0.18 points,
  all skill players together within 0.25; a QB's lost sack fumbles, which the draw charges him, are in his
  projection). A favourite **runs more and throws a little less** than an underdog, and the **game itself follows
  the score** (below); and **teams throw more early in a season and run more late** (`services/sim_calendar.py`, fitted on
  2018-2025: targets x1.02 in weeks 1-4 and x0.98 in weeks 15-18, carries the reverse, yards per target about 1%
  lower late; the belief learns calendar-free volumes); both apply before a game is centred on its line, so the
  points stay the line's and only the mix moves. The hidden strength, the widened edge and the drive count are
  tuned so simulated results against the sim's own line look like real results against Vegas (sim_eval
  2021-2025, 3 seeds, full / mid mode, real in brackets: line vs result correlation 0.45 / 0.49 (0.46 / 0.53),
  results around the line spread 12.6 / 12.9 (12.6 / 12.0), margins of 21+ 16.7% / 18.4% (16.9% / 17.2%), 21+ in
  the most lopsided fifth of lines 25.7% / 32.1% (25.1% / 29.0%; mid mode's 3-seed figure moves by 3 points from
  run to run, 30.5% over 8 seeds), in the closest fifth 13.2% / 13.0% (12.0% / 7.9%), team scores mean 23.2 / 22.3
  (22.5 / 22.6), sd 10.0 / 10.1 (9.9 / 10.1), ties 0.29% / 0.37% (0.29% / 0.19%); margins of 1-3 22.7% / 21.1%
  (24.9% / 23.7%), exactly 3 11.9% / 11.6% (14.6% / 13.6%), exactly 7 8.1% / 7.3% (7.7% / 7.5%), 4-8 28.7% /
  27.6% (28.3% / 29.5%); before the closing window 1-3 was 18.5% and exactly 3 8.0% in full mode). Known gaps:
  margins of exactly 3 are still 2-3 points short (the draw before the cut is smoother than a real game's first
  fifty minutes); records run a little extreme (wins sd 3.23 against 3.12, best record 14.9 against 13.8, worst
  1.7 against 2.8; a smaller hidden strength does not help once the line correlation is held, and the user's
  lighter injuries and rarer benchings keep strong teams whole); skill players score 0.2 / 1.9 fantasy points a
  team-game more than real (mid / full; 2.1 / 2.3 before, the full mode's rest following last season's scoring
  level, 23.2 points a team against 22.5) and shutouts are 1.3% / 0.86% of team scores against 1.4% / 1.1%; in
  the closest fifth of lines in the mid-season mode a 21+ blowout is more common than real (13.0% against 7.9%, a
  small real sample: full mode's real is 12.0%); a favourite's D/ST draws about half a point a game over its
  projection in a lopsided game (0.56 at 31-13; 0.52 before the closing window); and a tie's chance is a normal
  spread's, while real cut margins are lumpier, so it runs 9-24% under the drawn one in close and low-scoring
  games (0.01-0.04 points of overtime a side).
- **Games are played top-down,** so box scores always add up (`services/sim_game.py`, `sim_parts.py`): each
  team's plays half by half (below), split into targets and carries, then shared among the players in the day's
  game plan by their roles (an injured player's work goes to his teammates, a rookie takes some from everyone),
  then catches, yards and touchdowns per player. The quarterback's passing line is the sum of his receivers' lines, so a QB's completions, yards and
  TDs always match his receivers'. His attempts are his completions, his interceptions and the other
  incompletions (sacks are not attempts); an interception is drawn among the incomplete attempts, at the rate
  that keeps its mean the team's attempts x INT rate, so attempts never fall below completions + INTs. If he
  leaves hurt, his backup throws part of each receiver's catches (and the TDs among them) and the matching
  share of the incompletions; a closing-window or overtime TD catch is one more completion and attempt.
  sim_eval reports the starters' passing (2021-2025, 3 seeds, real / simulated): completion % 64.3 / 65.4 (mid)
  and 64.9 / 64.7 (full), attempts a start 31.8 / 33.1 and 32.2 / 34.0, INTs a start 0.72 / 0.75 in both
  (unchanged by drawing interceptions among the incomplete attempts). Kickers get an extra point try for each of their team's TDs and field goal tries that rise
  with the team's yards and fall with its TDs (drives that stall); a kicker who leaves hurt keeps only his
  share of the kicks. A defense's sacks and interceptions are the opposing offense's, and its points allowed
  are the opponent's points. Points use the league's own scoring, custom settings included. A busy passing day
  gets shorter catches (yards per catch x (targets / the team's usual) ^ -0.3, `game.ypc_volume`, matched to
  the real spread of single-game passing yards): with volume and efficiency drawn apart, 2027 leagues had 21.6
  400-yard passing games a season against a real 8.8, 3.25 500-yard ones against 0.4, and a 716-yard game (the
  real record is 554); now (full-mode gate, 2021-2025, 3 seeds) 11.7 and 0.7, the season's top game 519 yards
  against a real 491 (see the passing game of a whole season above). Weeks 12-18 simulated from a real season
  had 5.9 400+ yard games against a real 2.6 because they kept September's passing into December (real teams
  pass about 7 yards a team-game less in weeks 12-18 than in weeks 1-11); with the calendar (`sim_calendar`)
  they have 3.5.
  `sim_eval` reports these tails beside real seasons (400+ and 500+ yard passing games, the
  season's top single-game passing, rushing and receiving yards, targets and carries, 200+ yard games, and
  team-games without a QB line, real 0), and in full mode the season's shape (sim_metrics_shape: 4,000-yard
  passers, 1,000-yard receivers and rushers, the WR and TE leaders, target and carry shares, yards per target
  spread) and the standings (best and worst record, team points spread, games decided by 1-3 and by 8 or fewer).
- **Play calling follows the score** (`services/sim_script.py`, fitted on 2018-2025 play-by-play by
  `python -m services.sim_script_fit --fit` -> `sim_script.json`; run it before `sim_closing_fit`). A game is drawn
  in three parts: the first half, the second half up to the closing window's cut, and the window. A half's plays
  are a Poisson count around its share of the team's volume (real: 50% / 30% / 20% of targets and carries), split
  into targets and carries at the team's pass share plus a small lean for the day (a pass-first or run-first plan,
  sd 0.027, measured beyond the binomial within team-seasons; a team's pace adds nothing beyond a Poisson's spread
  of its plays: real plays before the cut spread 6.8 a game within a team-season, a Poisson's 6.9). The second
  half's mix follows the score at half time (fitted within team-seasons: per point of lead, targets -1.1% and
  carries +0.65%, so a team down two scores after the half throws about 20% more and runs about 13% less; clipping
  the lead at 10, 14 or 21 did no better on held-out seasons), both halves also per point of the pregame line
  (targets +0.43% / +0.57%, carries +0.22% / +0.40%). The window's plays are the real ending's own (sim_closing.json's
  endings carry their targets, carries and kneel-downs, scaled by the team's volume): a leader runs out the clock and
  kneels, a trailer throws. Only the halves' plays score (their TD rates, interceptions and sacks per play divided
  by the halves' share; the window's scoring is its real ending's), so team points stay on the line, and every
  projection is the draw's mean: the second half's response uses the projected lead at half time plus how far each
  side's first half beat its own mean, which averages out. The game's touchdown drives (`game.td_drives`) are split
  between the halves and a half's field-goal tries move per TD and per yard as much as a whole game's do (with each
  half's own base, results around the line spread 0.5 points too wide). Second quarters respond to the first
  quarter's score too, but weakly (targets -0.55% and carries +0.21% a point), so the game is not cut into
  quarters. sim_eval's usage rows (`sim_metrics_usage`, per team-game; real / before / after, mid then full mode,
  2021-2025, 3 seeds): corr(final margin, carries) 0.51 / 0.34 / 0.46 and 0.52 / 0.29 / 0.49; corr(margin, targets)
  -0.18 / +0.03 / -0.22 and -0.23 / +0.05 / -0.28; corr(carries, targets) -0.37 / -0.19 / -0.27 and -0.40 / -0.18 /
  -0.22; team carries sd 7.5 / 9.0 / 7.3 and 7.6 / 8.4 / 6.7; carries when losing by 14+ 21.5 / 23.7 / 22.9 and
  20.8 / 23.7 / 22.8, when winning by 14+ 32.8 / 31.8 / 32.0 and 32.3 / 31.1 / 31.7; targets when winning by 14+
  28.5 / 32.5 / 28.2 and 28.2 / 33.1 / 28.5; a loss by 14+ with 30+ carries (a team losing 28-6 does not run 30
  times) 9.7% / 19.3% / 11.6% and 7.0% / 19.5% / 10.7%; carries + targets a team-game 58.5 / 59.2 / 58.6 (mid).
  Results against the line hold (after, mid / full, real in brackets): line vs result r 0.50 / 0.46 (0.53 / 0.46),
  results around the line sd 12.7 / 12.4 (12.0 / 12.6), margins of exactly 3 12.1% / 11.3% (13.6% / 14.6%), team
  score sd 10.2 / 9.9 (10.1 / 9.9).
- **Work is shared like a real team's.** Real depth players are lumpy, no touch in many games and several in a
  few, backs most of all: a healthy back the belief expects 3-4 touches of has none in 46% of his games, a
  receiver in 17%, where a draw around their means leaves them without one in about 3%. So each week a player is in
  the game plan with a chance fitted by position on the touches the belief expects of him (`participation` in
  sim_script.json: q = 1 - c exp(-touches / scale); RB c 0.92 and scale 3.5, WR 0.92 / 1.75, TE 1.0 / 0.75; fitted on
  2019-2025, the belief before weeks 6, 10 and 14 against the next four weeks; held-out seasons score better with
  it); in the plan his weight is divided by q and out of it he gets nothing, and the players always in the plan
  (a chance of 0.97 or more, or a quarter of the team's targets or carries) take up the difference, so every mean,
  and every projection, is the belief's. The belief's own depth weights were checked and are not too high (its
  expected touches match or trail real ones at every depth rank, 2019-2025). Usage rows (real / before / after,
  mid then full): players with a target 7.8 / 8.6 / 7.8 and 7.9 / 8.2 / 7.6; non-QBs with a carry 3.0 / 3.6 / 3.2
  and 3.0 / 3.4 / 3.1; backs with a carry 2.4 / 2.7 / 2.4 (both modes); the game's third back's share of the
  non-QB carries 0.04 / 0.09 / 0.08 (both); the targets after the fifth receiver's 0.13 / 0.18 / 0.15 and 0.14 /
  0.15 / 0.13. A quarterback's **kneel-downs** are his carries, as the box score counts them (the window's real
  endings carry them: about 0.75 a team-game, -1.07 yards each), and his other carries run at his rates without
  the kneels in them (x1.31 yards and x1.25 TDs a carry, measured on 2018-2025 play-by-play), since the belief
  learns his rates from box lines that count the kneels: QB carries a team-game 4.0 / 3.4 / 3.9 and 4.1 / 2.7 / 3.3,
  QB rushing yards 17.5 / 15.5 / 18.8 and 18.2 / 12.6 / 15.3. (Simulated weeks 12-18 against every real week
  overstated the gap: real QBs carry 3.8 times a team-game in weeks 12-18.)
  Still short: the game's lead back takes 61% of the non-QB carries against a real 67% (a back with 20+ carries
  in 16% / 18% of team-games against 23% / 20%), because a real lead back misses about 7% of his healthy games
  untagged or while listed Questionable / Doubtful (his backup then leads), and simulated absences are always
  tagged Out; a team losing big still runs about 1.5 times more than a real one (real teams that fall behind also
  run fewer plays: the draw's plays do not depend on how its drives go); full mode's team carries spread 6.7
  against 7.6 and its corr(carries, targets) is -0.22 against -0.40 (simulated teams' volumes vary less game to
  game); players with only 1-2 targets 2.9 / 2.8 against 3.2; targets per point of the line -0.30 / -0.35 against
  -0.23 / -0.09 (the window's real endings move more with the expected lead than a team's window does with its
  spread; carries per point 0.91 / 0.77 against 0.87 / 0.88); 21+ wins in the closest fifth of lines in mid mode
  13.9% against 10.3% before and 7.8% real (8 seeds: 13.1% against 10.6%); a starting QB throws about 1 attempt a
  start more than a real one (team attempts match, targets + 1.6 a team-game in both), as real starters share late
  passes in blowouts with their backups.
- **Injuries** come from what really happened to fantasy-relevant players in 2018-2025
  (`services/sim_injuries.py`, rates in `sim_injuries.json`): by position, the chance a player who plays a
  game gets hurt in it, how many games he then misses (the real lengths, season-enders included), how much
  of the game he finished, the body part, how often he comes back listed Q, and how often a healthy starter
  is listed Q or D and plays anyway (who counts as a starter there comes from the public belief, never the
  truth). A long absence is tagged IR, like the real reports. Defenses are never hurt. A player already out
  when the simulation starts sits a fitting number of further games; a suspension (SUS) or exempt-list (EXE)
  absence carries on too, keeping its tag and with no Q on his return (lengths from the week-to-week table for
  a suspension, whose real length the data doesn't hold, and from the long-tailed IR table for the exempt
  list, which is open-ended). A live league also reads each player's **Sleeper status once, when it is
  created** (`sim_setup.with_status_now`): Out, Doubtful, IR, PUP, suspended (and Sleeper's other zero-chance
  statuses) become a hard tag in the first simulated week, so a player hurt in the last real game (Drake Maye,
  2026 week 10) starts the simulation out, for a length the injury model draws; Questionable is left to the
  model. Out / Doubtful statuses older than 18 hours (their count is logged, at WARNING when most statuses
  are that old: the sync has stopped) and players already hard-tagged
  in the last real week are left alone; IR, PUP and suspended last for weeks, so they count however old the
  sync is, except a stale one fetched before the kickoff of any game he then played (he has been activated
  since, a bye week after it or not; a tag fetched after that game, hurt Sunday and placed on IR Tuesday, still
  counts). Real weeks never change.
  The chance a player is hurt in a game is fitted on 2021-2025 only (`HAZARD_SINCE`; how long an injury lasts
  uses every season): the same count gives quarterbacks 0.029-0.036 new injuries a game in 2018-2020 and
  0.043-0.061 in 2021-2025, and the pooled fit left simulated QBs at 0.034 against the 0.042 target; now 0.041.
  The step looks like a recording difference, not football: a quarterback's missed games carry no tag at all
  about a third of the time in 2018-2020 (0.29-0.38) against about a fifth later (0.10-0.33), and IR tags with no
  injury text (where the COVID list lands) take 0.002-0.008 off the QB rate in every season, 2021's included
  (0.060 -> 0.053), so starting the window in 2022 would give the same rate. The refit read today's database,
  re-imported since the last fit, so other fitted values moved a little too (a kicker's Q/D tags on games he
  played 0.8% -> 1.4% of games, a kicker listed Q playing 55% -> 66%, a back's preseason injury rate 3.3% -> 3.0%,
  the preseason length table and the IR shares).
  On purpose, simulated players get hurt **about a quarter less often** than real ones (`INJURY_SCALE = 0.75`
  in `sim_injuries.py` multiplies the per-game and preseason injury chances, in whole simulated seasons and
  in the live season's simulated weeks alike); how long an injury lasts stays real, and an absence already
  running in the real weeks is carried over untouched. `sim_eval` reports, by position, new injuries per
  game played and the share of games missed for the real seasons, the target (real x 0.75) and the
  simulation.
- **Hidden talent:** every player's true role and efficiency differ from his reputation by a random draw,
  widest for rookies and players with few games, smallest for veterans with a full season
  (`services/sim_talent.py`); teams' volume and defenses move the same way. During the season some
  players **break out** (a backup's role grows), some **bust** (a lead player's shrinks, but never one of the
  top 12 at his position by the public's rating: real stars lose work to injuries, not to a coach's whim).
  **Rookies grow:** about a quarter of the running backs, receivers and tight ends in their first season get a bigger role
  around mid-season (x1.6 from a week between 8 and 11), fitted on real rookies 2019-2025 against veterans
  with the same first half (real rookies gain about 6% more role than such veterans after week 9, the
  small-role ones about 15%; their points per touch do not grow). In the live season only the simulated
  weeks can carry a step; the public learns it from the games. These spreads and rates are tuned so a
  simulated season reshuffles like a real one (preseason rank against final rank, breakouts, busts).
  A season simulated whole also draws each veteran's role for the year (what his position's role curve leaves
  unexplained from one season to the next, `role_sd` in `sim_next_year.json`: about 0.43 on the log scale for a
  receiver, a quarter of it used, `talent.season_roles`; a rookie's around the role a player taken at his pick
  had, all of what the rookie role line leaves unexplained, 0.65-0.75, as the public knows only his pick: with a
  quarter of it, as for veterans, rookies followed their draft slot too closely; full mode 2021-2025, real /
  before / after: rookie ADP against finish 0.50 / 0.66 / 0.53, rookie top-24 finishes 8.4 / 6.8 / 8.0,
  breakouts 10.6 / 7.5 / 7.9; with the top-12 floor below), and its belief takes 22% of the targets (20% of the
  carries) from each team's depth beyond its top five receivers (top two backs) to give to them
  (`talent.depth_targets` / `depth_carries`): a belief carried over from the end of a season spreads a team's
  work too thinly. With the whole-season efficiency spread halved (`talent.season_spread` 0.5), on the
  full-mode gate (2021-2025, 3 seeds; real / before / after): 1,000-yard receivers 24.0 / 16.1 / 19.4, rushers
  13.6 / 11.0 / 12.5, top five receivers' share of a team's targets 0.74 / 0.71 / 0.74, top WR yards 1,811 /
  1,870 / 1,887, top TE yards 1,230 / 1,157 / 1,175, the spread of yards per target 1.34 / 1.74 / 1.55, WR / RB /
  TE top-12 points a game 14.8 / 16.1 / 9.7 against 14.7 / 15.1 / 9.0 before and 15.4 / 16.0 / 9.6 after. Still
  short: the simulated whole season is more predictable than a real one (ADP against finish, WRs 0.73 against
  0.60; breakouts 7.1 against 10.6):
  more role noise brings that closer but sends the top receivers past real leaders' seasons.
- **The passing game of a whole season.** The August belief regresses a receiver's yards per catch and catch
  rate and a quarterback's passing quality by their measured year-to-year keep (the slope of next season's rate on
  the end-of-season belief's, 2019-2025, full seasons: yards per catch WR 0.58, TE 0.52, RB 0.16; catch rate
  0.52; passing quality 0.54; `FULL_KEEP` in `sim_next_year.py`), whatever share the whole-season calibration
  picks for the other rates (0 for all four positions now). Carried over unregressed and then drawn again in the
  truth, they spread a team's season passing too wide (team yards per target sd 0.92 against a real 0.71). A
  team's true volume also leans against its offense's shared efficiency draw (`talent.volume_eff_rho` -0.25):
  real teams that pass well do not also pass more (season targets a game against yards per target, r = -0.27;
  simulated -0.25, was -0.22). With the truth closer to the belief, the truth's edge over the line
  (`talent.edge_scale` 1.05 -> 1.12) and the depth discount (0.85 -> 0.78) were retuned on the same gate; with
  the closing window in, the edge is 1.08: over 8 seeds of the full-mode gate, 1.12 put 28.0% of the most
  lopsided fifth's games at 21+ (real 25.1%; ef3f26e 24.5%), 1.08 puts 25.9% with the line's correlation with the
  result at 0.44 (real 0.455) and results around the line at 12.60 (12.60), and 1.04 put 24.5% but the
  correlation at 0.43.
  Whole-season gate (2021-2025, 3 seeds; real / before / after): the season's passing leader 4,963 / 5,300 /
  5,106; a team's season passing sd 495 / 548 / 493 (most 4,997 / 5,335 / 5,144); 400+ yard passing games 8.8 /
  15.9 / 11.7 a season, 500+ 0.4 / 1.5 / 0.7, the top game 491 / 522 / 519; 4,000-yard passers 8.2 / 12.3 /
  11.3; 1,000-yard receivers 24.0 / 19.4 / 19.5, rushers 13.6 / 12.5 / 12.7; 21+ point wins in the most
  lopsided fifth by the line 25.1 / 23.5 / 25.0, line r 0.46 / 0.45 / 0.44; QB top-1 / top-6 / top-12 points
  a game 23.9 / 21.3 / 19.6 against 24.9 / 21.1 / 19.4 before and 24.2 / 20.6 / 19.1 after. Over 8 fresh 2027
  leagues: the season's leader 5,460 -> 5,242 (4,874-6,137), 4,000-yard passers 10.9 -> 10.0. Over 40 fresh
  2027 leagues now (after the closing window, the line's run / pass mix and the calendar): the leader averages
  4,987 yards (real 2018-2025: 5,008), 95th percentile 5,388, most 5,596 (real most 5,316); the leader's lead over
  the 5th passer 635 (real 551). Still high on the full-mode gate: 4,000-yard passers 12.0 against 8.2 and 400+
  yard games 11.0 against 8.8, because a whole season is built from the season before and real passing fell
  every year 2021-2025 (261 yards a team-game in 2020, 225 in 2025): the simulated 2021-2025 average, 242, is the
  real 2020-2024 average, 242 (last season's level is still the best guess of the next: a two-season mean
  predicts 2019-2026 worse); in 2023, the one season whose level the season before matched, 4,000-yard passers
  were 10.5 against 10. The leaders' tails run long: over 40 full-mode seasons the passing leader's 95th
  percentile is 5,665 yards against 5,303 (real 2021-2025) and the receiving leader's 2,206 against 1,919.
- **Who starts at quarterback** (`services/sim_qb.py`, `sim_qb_jobs.py`): each team has a **job holder**.
  He starts whenever he is healthy; when he is hurt the next quarterback in line fills in, and when he is
  healthy again he gets the job back. An **established starter** never loses the job while healthy and always
  gets it back (the user's choice): his preseason quarterback rank this season is 12th or better on a price that
  ignores the games he is expected to miss (a real season: its ADP; a simulated next season: the per-game value,
  as its ADP is discounted for an injury carried into it), or his real preseason ADP was a top-8 quarterback's
  in one of the two seasons before. So Patrick Mahomes, 15th by real 2026 ADP after his knee injury, is
  established in 2026 and 2027 through his earlier ADPs. Of the real quarterbacks who lost a job healthy in
  2019-2025, four would have been protected: Carson Wentz 2020 (12th by ADP), Matt Ryan 2022, Justin Fields 2024
  (a fill-in kept the job on his return) and 2025. Everyone else can lose the job, at weekly chances fitted on
  real weeks 2019-2025 (`python -m services.sim_qb_fit --fit` -> `sim_qb.json`, every holder who was not
  established): a **benching** depends on his preseason ADP rank, how he has played in his starts so far against
  his projections and his team's record (21 job losses in 1,231 healthy weeks without a drafted rookie behind
  him), and is **halved** (the user's choice: veterans benched for veterans rarer than in real life): about
  0.13% a week for the 20th-ranked starter playing to his projections, about 4.6% for an unranked one, more when
  he plays badly. A **drafted rookie** (pick 64 or better) behind a starter who is not established takes over at
  the real, unhalved chance, sooner with a higher pick, a losing team and a struggling starter (7 takeovers in 87
  weeks): about 5% a week for the 5th pick on a .500 team, about 16% a week on a team that has lost three games
  in four; behind an established starter he sits all year unless the starter is hurt (and in August he is never
  named over one). A **young first-rounder** (drafted in round 1 in this season's draft or one of the three before)
  never loses the job to a rookie, and as a rookie holder is not benched healthy at all: real 2019-2025, such a QB
  holding the job in his first season was never benched healthy (0 of 197 weeks), in his second 3 of 268, and no
  team with one had a QB drafted in the first two rounds behind him. Over 200 2027 seeds, healthy job changes to a
  rookie: Cam Ward 91 -> 0 (4 before the return path was closed too), Jaxson Dart 88 -> 0; Tyler Shough (a
  second-rounder) and Drew Allar (third) still can lose theirs (95 -> 101, 75 -> 92). Teams changing their
  healthy starter twice in a season: real 2019-2025 4.4 a season, simulated 0.45 -> 0.28; three times or more:
  real 1.4, simulated 0.22 -> 0.03 (the real counts include untagged rests and game-day scratches the
  simulation does not have), so no damping of repeated changes is added. A **fill-in keeps the job** from a starter back from injury at the real chance in his ADP
  rank, halved (13 of 57 returns), and both of them get the true line that week ("Healthy again, but X stays the
  starter; he is the backup." / "Stays the starter: ... is healthy again and the backup."; a benching: "Benched:
  X starts in his place." / "Named the starter over ..."). Starts over the last three seasons, production, draft pick, years since
  the draft and expert ranks were also tried on the benchings; leave-season-out they add little or nothing the
  market has not priced in (and starts points the wrong way: given his ADP, a veteran with many starts goes
  sooner). In the live season the first simulated week's holder is an established quarterback of the team if it
  has one (out if he did not play the last real game), else the last real starter, unless he only filled in
  for a holder who was tagged hurt; today's injury status then sits whoever is hurt. Nothing is decided in a
  team's bye week. `python -m services.sim_qb_fit --check --season 2027 --seeds 200` simulates leagues the way
  they are built and reports the job losses. A QB who has just taken a job can lose it straight back: real teams
  undo a healthy benching (the old starter sat untagged that week and the two after) within four weeks about one
  time in five (2019-2025: 15.1 such benchings a season, 3.0 undone; 6 of the 19 rookies who took over gave the
  job back, as Tua Tagovailoa and Ryan Fitzpatrick traded it twice in 2020), and simulated seasons undo
  fewer (200 seeds: 2027, 12.5 a season and 1.0 undone, 7.7%; 2026 from week 12, 7.9 and 0.2), so no grace period is added (`sim_qb.swap_backs`, reported by
  the check).
- **Fill-in quarterbacks** (`replay_fillin.without_news`: the trade chart's one point-in-time definition, read
  from the games: a QB starting while his team's regular starter carries a hard tag, one whose regular is just
  back, or one who took the job, until his fifth start with the regular not expected back; a preseason IR or PUP
  tag counts as an out-for-the-season report there): the public trusts only 15% of a fill-in's passing-quality lead over a typical backup
  (`market.fill_in_trust`, applied to the public belief, never to the truth), and the weekly experts never rank
  him above QB16 (`FILL_IN_BEST`); his rest-of-season value already counts only the games he is expected to
  start. Both come from real fill-ins (runs of 3+ starts behind a tagged holder, 2019-2025, `python -m
  services.sim_qb_fillins --real 2019-2025`): after 2, 3 and 4 starts their projection was top-12 3.3%, 2.3% and
  6.7% of the time (best tenth QB14-16), their median expert rank 32, 30.5 and 28, and no real fill-in reached the
  expert top 15 (best QB16: Joe Flacco and Joshua Dobbs, 2023); they scored 13.1 half-PPR points a game. Simulated
  (`--sim 2027|2026 --seeds 200`): top-12 projections 8.9%, 8.4% and 9.4% in 2027 and 11.3%, 7.5% and 10.6% in
  2026 from week 12 (best tenth QB12-14), median expert ranks 33 and 30, expert top 15 after four starts 0.4% of
  runs (one run in each season, a QB the engine has as his team's regular holder whom the measure, which has no job bookkeeping, reads as a fill-in), and 13.6-13.9 points a game: about 0.7 more than real, the same lean as the simulated skill
  points as a whole (see the game section), and within the real spread (sd 3.8 over 61 real runs).
- **Buzz:** before the week a breakout or bust starts, a news line is published for 60% of
  them ("Beat reporters say his role is growing; expect more work.", "Reports say his snaps and touches are
  shrinking."); a quarterback benching gets its true line the week it happens (above), never a rumour that
  week or after, and a top-12 player at his position never breaks out or reads that his role is growing (a star
  back whose role weight dipped while he was hurt is still a star). "Reports say his starting job is in
  danger." comes the week before a benching for 60% of the holders whom next week's seeded draw benches at this
  week's chance (form and record move little in a week), and as a false line about a quarterback whose job really
  is at risk: over 40 2027 leagues 66% of those lines were followed by the benching within two weeks (all were
  false before). False lines are added so only about 60% of
  all lines are true, in the same weeks a change can start (never the first simulated week or the last two).
  A false line is only about a change that could plausibly happen to him, as often as it really does: a
  backup's role growing, a lead player who is not a top-12 star losing work, or a starting quarterback whose
  job really is in danger (not established, with a weekly chance at least the real average
  for such starters; weighted by his chance to lose it), so an established starter never reads "his starting job is
  in danger". It shows as a small **Buzz** chip (named "Buzz: ..." for screen readers and voice control, tooltip "Buzz (a rumor): ...") on player rows
  and the draft board (press it to open his card), and under **News** on his card. It is the only way the truth reaches the screen
  before the games, and it is right only some of the time.
- **Projections, expert ranks and ADP** are computed from the belief each week (`services/sim_market.py`):
  the belief is refitted from the games played so far (recent games count more), a projection is a
  player's expected points in that week's game with the players ruled out removed (so their teammates
  rise), expert ranks are each position ordered by expected value (with each team's scoring from the line
  model, as experts know which offenses score) with a little noise (about 8%, a third of that at quarterback,
  whose values are bunched), and ADP is
  a 12-team draft in the league's scoring by points over replacement; kickers and defenses, and on a 1-QB
  list the quarterbacks, take the real average ADP of the Nth one drafted (1-QB PPR lists 2021-2025: QB12
  near pick 96, QB24 near 162, QB32 near 198), so every team's believed starter is drafted as a starter (a
  superflex list keeps points over replacement, which already takes all of them by about pick 150). Going
  into a whole simulated season the market also leans on last season's points a game, leaving out a game a
  player left hurt (under half the snaps, then a hard tag), and the **preseason expert ranks** (what the draft room's Rank order comes from)
  follow that ADP with the same ~8% noise, because real August experts and ADP agree closely (rank
  correlation 0.98 over the real top 150 in 2022-2025; the simulated ones 0.99). The weekly experts **revise slowly**: each player's noise carries from week to
  week (90% of last week's), and the value a healthy player is ranked on moves 40% of the way to this week's
  (on injury news, a player ruled out or back, or a teammate at his position ruled out or back, is re-ranked
  at once: the backup whose starter just went on IR moves up that week; a quarterback benching is news for
  both of the team's quarterbacks the same way), so a healthy star does not jump from QB1 to QB9 and back: a
  healthy top-16 quarterback's expert rank moves about 1.2-1.5 places a week (real: about 1.1). Quarterback expert ranks still agree less with the week's projections than real ones (rank correlation
  about 0.6-0.68 against 0.72-0.75): QB values are bunched, and the projections follow each week's matchup line.
  **At a mid-season join** (the live season's first simulated week) the experts' memory starts from the last real
  week's ranks: each player gets the opinion that makes this week's values give his real rank, which then carries
  on and drifts as usual, so nobody jumps from QB28 to QB8 with no news (sim_eval mid, 2021-2025 from week 12:
  the rank change across the join now equals a real week's, QB 1.25 / RB 1.35 / WR 2.50 / TE 0.95 against real
  1.25 / 1.33 / 2.49 / 0.96). A jump-in league's market ADP leans on the same opinion (the price multiplier that
  opinion gives the belief's values, `sim_market.expert_lean`), so its draft board (the experts) and its ADP
  agree: rank correlation of the top 60, real 2026 data, 0.99-1.00 (0.77-0.80 with a market on the belief alone).
  The **published projections** start at the real sources' level at a join and settle to their average bias:
  real sources project high (2019-2025, actual minus projection QB -0.49, RB -0.50, WR -0.19, TE +0.03, about
  2-6% at every level; the belief's own are unbiased), and at the join they sit about 9% above the belief's for
  the healthy top players. The first simulated week takes that level (`market.join_scale`, QB 1.08, RB 1.09, WR
  1.10, TE 1.06) and it fades by half a week (`market.join_fade`) to `market.projection_scale` (QB 1.04, RB 1.05, WR 1.03,
  TE 0.99), set by the average bias; a season simulated whole uses that scale throughout. A weekly projection
  also follows the week's line only partly: it keeps `market.line_follow` (QB 0.6, RB 0.42, WR / TE 0.3) of the
  move from his projection at the team's typical points (the team alone against this
  opponent's pass and run defense, scaled to its points against a league-average defense: the matchup without the
  line, `sim_market._anchor`) to his projection under the line, because real ESPN /
  Sleeper projections follow a team's implied total at 0.13-0.56 by position (`sim_metrics_join.line_follow`:
  QB 0.47, RB 0.33, WR 0.16, TE 0.17 in weeks 12-18, QB 0.56, RB 0.32, WR 0.21, TE 0.13 over whole seasons)
  and the full expectation followed it at 0.43-0.79, so simulated projections churned more than real ones. On
  sim_eval 2021-2025, 3 seeds, mid / full, real in brackets: how far projections follow the line QB 0.54 / 0.53
  (0.47 / 0.56), RB 0.30 / 0.35 (0.33 / 0.32), WR 0.15 / 0.20 (0.16 / 0.21), TE 0.16 / 0.05 (0.17 / 0.13); actual
  minus projection -0.50 / -0.36 (-0.35 / -0.39; before -0.66 / -0.80), the join's projection ratio 0.98-1.00
  (0.98-1.00), median week-to-week change of a healthy top player's projection QB 1.16 / 1.18 (1.28 / 1.30;
  before 1.91 / 1.82), RB 0.94 / 1.05 (1.18 / 1.13), WR 0.77 / 0.93 (0.75 / 0.79; before 1.22 / 1.29), TE 0.56 /
  0.64 (0.66 / 0.66): a little steadier than real for QBs and RBs. The truth and the
  values experts and the market rank on are untouched.

**2027 specifics:**

- **Aging:** each veteran's role grows or fades by his position's curve for his years in the league (1, 2,
  3, 4-5, 6-8, 9+), big and small roles move partway back toward the usual one, and each team's
  returning players are rescaled so the curve moves work between teammates without changing the team's
  total. Efficiency regresses toward the position norm, a quarterback's rushing with its own (much weaker)
  regression measured on quarterbacks: a scrambler keeps most of his edge. A receiver's yards and TDs per
  catch are split into his own part and his quarterback's: his own regresses, the quarterback's moves with the
  August starter's passing (so a great passer's receivers stay good, and a QB change moves them). How much of
  the measured regression the preseason belief applies is fitted on whole seasons: each real season 2021-2025
  rebuilt from the year before, against what players really scored. Applying all of it left the August
  belief too flat (real stars outscored it by 1.5-3 points a game); the fitted share is none at every
  position (the end-of-season belief is already shrunk enough), which made the preseason values match real
  points a game (slope about 1, lower error). The measured regressions still size the hidden talent's
  offseason spread (what last season leaves unexplained). All of it is measured from real seasons 2019-2025 (`sim_next_year.json`). On each
  team the QB who started the most games last season is the starter again in August; starts made for
  another team count only when he was that team's main starter (a five-game fill-in elsewhere does not take
  the job from the team's own starter, a signed starter does). A quarterback traded in mid-season keeps his
  old team's starts too, unless his new team's main starter opened its season and is still there: Jacoby
  Brissett's Arizona starts do not beat Lamar Jackson in Baltimore, while a starter traded at the deadline to
  a team led by a fill-in keeps the job.
- **Rookies** get the role a player taken at that pick and position really had as a rookie, and the widest
  talent draw. A rookie QB starts week 1 when his team has no starter, or when his pick makes it likely and
  the incumbent is a below-average passer; otherwise he is next in line. Real drafters pay for a rookie's
  upside, so the market (ADP and the preseason expert ranks, not his role or talent) multiplies his value by
  a premium fitted on the real rookie ADPs of all of 2019-2025 at once, then checked on held-out seasons
  (`rookie_premium` in `sim_next_year.json`; allowed to fall with the pick but never to rise). The fitted
  premium is flat by position, the same at every pick: x1.28 for a running back, x1.16 for a receiver,
  x1.11 for a tight end, none for a quarterback (a 1-QB list takes the Nth QB at the real Nth QB's pick, so
  a rookie QB's price is his rank among the quarterbacks).
- **The offseason file** `backend/sim_data/offseason_2027.json` is meant to be edited by hand. `rookies`:
  one entry per player with `name`, `position` (QB/RB/WR/TE/K), `team` (schedule code, e.g. MIA, LA, LV,
  WAS), `pick` (projected overall pick, 1-262: the prospect's rank on the consensus board), `college`, and
  optionally `lock` (default `false`; `true` pins him to that `team` and `pick` in every league) and `hype`
  (1-3, default 1) for a prospect rated beyond his pick: the market's rookie premium and his expected role
  are both multiplied by it (his talent is still drawn around that role, with a rookie's wide spread, so he
  can still bust). Jeremiah Smith carries 1.2, set so his average ADP across leagues lands near where Marvin
  Harrison Jr.'s did as a rookie (14.5 in 2024's PPR ADP): over 20 league seeds his ADP averaged 12.3 (1-30),
  and he finished a top-12 receiver 35% of the time, top-24 55% and outside the top 36 20% (measured when the
  2026 standings were still open: the seeded draft put him on 13 different teams; with 2026 real through week 11
  the bottom of the standings is clear, and the consensus first pick goes 1st-3rd to Tennessee, Miami, the
  Chargers or Atlanta, 8, 5, 5 and 2 of 20 seeds). `moves`: `name` (as the app spells him),
  `position`, `team` (the new team, or `null` for retired / out of the league) and a `note`. Names must be
  unique across both lists; everyone else stays on his team. A bad entry greys out the 2027 card with the
  file name and the problem; a move whose name matches nobody is skipped (logged as a warning). Saved
  leagues keep the offseason they were built with.
- **A seeded draft** (`services/sim_rookie_draft.py`): unless `lock`ed, where a rookie lands depends on the
  league's seed, decided once when the league is created (same seed, same draft). The 2026 standings are
  finished from the real results so far, each game left won with a probability from the two teams' points
  differential a game; the draft order is fewest wins first. Each prospect's board spot is his `pick` x a
  random ~30% miss, and the team on the clock takes the best prospect whose spot has come up and that it
  needs: a QB unless its August starter (as above, after the moves; not a starter hurt late, whose recent
  starts look like a backup's) is established (a full season's evidence, average-ish passing), an
  RB/WR/TE when its room is below the league's median. A consensus top pick goes near the top regardless;
  a QB slides past teams that don't need one through round 2. A team with a young first-rounder at QB (drafted in
  round 1 in one of the last three drafts, from the cached nflverse picks) needs none, and no team takes two QBs
  in one draft. A drafted rookie QB (pick 64 or better) behind a starter who is neither established nor a young
  first-rounder is priced on the share of the season the fitted weekly takeover chance gives him at a .500 team
  and par form, x his value as the starter (about pick 145-210 in 2027 leagues); his weekly projection stays a
  backup's until he starts, and behind a protected starter he has no ADP. The drafted pick then sets his rookie role,
  his market premium and his place on his new team's depth chart. Shortcut: playoffs, strength-of-schedule
  tiebreaks and traded picks are not modeled.
- **Every team has a backup quarterback:** each team's real quarterbacks of today (the depth chart, else
  Sleeper's depth order) join a live or next season's pool, the offseason file's moves and retirements on top
  (2027: Joe Flacco retired leaves Cincinnati with Joe Burrow alone); only a team still short of two gets a
  generic one ("CIN backup QB"). Each is a backup with no games of evidence (a backup's passing, a token
  projection so a manager can add him). If every QB of a team sits in a week, the generic one starts, else the
  one due back soonest, his tag lifted that week (an emergency start): no simulated game is ever played without
  a quarterback (before this round 3-17 team-games a 2027 league had receivers catching passes nobody threw).
- **Starters and split backfields:** each team's starters come from the belief (role weights fitted on
  2025-2026 games, a QB's starts and passing quality), so a veteran with a track record keeps his job and a
  rookie is next in line unless his draft capital says otherwise; when a starter is hurt his backup plays,
  and the backup's card says why ("Expected to start while Justin Herbert is out (IR, Knee).").
  A team whose RB2 has 60%+ of its RB1's work opens with a **committee** (`services/sim_depth.py`); in
  2018-2025 about a quarter of teams did, about half of those committees were won during the season, the
  early leader won about three in four, mostly in weeks 5-9, and the loser kept about 0.38 of the winner's
  work (`python -m services.sim_depth --fit --seasons 2018-2025` -> `sim_depth.json`). In a simulated
  season the winner and the week are seeded; the public learns it only from the games (and the usual
  60%-true buzz).
- **Injuries carried over** (`services/sim_carryover.py`): a player still on the injury report at the
  newest 2026 week starts 2027 healing. His injury (the live status's detail when there is one, e.g.
  "Knee - ACL" + "Surgery", else the report's body part) gives a typical return time (ACL and a repaired
  Achilles about 48 weeks, a pectoral or PCL 20, a Lisfranc 26, a hamstring 4, ...; a generic knee, foot or
  ankle operation doubles its entry), counted from the week he was hurt to 2027's week 1. The market prices
  the expected games missed (his teammates gain meanwhile), his card's news says when he is expected back,
  and the truth draws his real recovery around it (lognormal, a quarter of the time), so in some leagues he
  is ready for week 1 and in others he opens on IR with the usual noisy "expected back week N" reports.
  `python -m services.sim_carryover --check --seasons 2018-2024` compares the model with real cross-season
  returns: of the 493 players still tagged at the end of a 2018-2024 season who played the next one,
  34.9% missed its week 1 against 25.4% of healthy players (box scores also miss depth players, so only
  the ~10-point gap means anything). The old reports carry only coarse body parts ("Knee"), which this
  table reads as short injuries, so the check cannot validate the detailed entries (the table rests on the
  published return times) and that ~10-point excess for generic injuries is not modeled.
- **Schedule:** until the real 2027 schedule is in the database, it is built by the NFL's rotation
  formula (`services/sim_schedule.py`: 6 division games, a division of each conference, two same-place
  games and the 17th game), seeded by the season so every 2027 league shares it; the same-place games use
  the 2026 standings so far. One bye per team in weeks 5-14 and division games only in week 18. Once the
  real schedule is loaded, new leagues use it.

**Known limits** (the modules' `shortcut:` notes):

- A player's years in the league count from his first season in the database (2018), so a veteran who
  started before 2018 ages as a younger player until the oldest bucket.
- The generated schedule picks home and away within a rotation at random (not the real alternation), puts
  the weeks in a random valid order, and plays every game on Sunday (no Thursday or Monday games).
- A simulated injury's first missed week is always tagged O or IR, never a game-day Q/D ruling or PUP
  (every screen treats them the same).
- The live season's preseason ADP is MyFantasyLeague's PPR list for every format until Fantasy Football
  Calculator has one. MyFantasyLeague's ADP request can't be limited to drafts held before week 1, so a list
  first fetched in-season also counts a few late drafts (a little hindsight in a **Full season** draft).
- The seeded draft ignores playoffs, tiebreaks and traded picks, and its board noise and need rules are set
  by hand, not fitted on past drafts.
- The rookies and moves in the 2027 file are a snapshot of mock drafts and reports (dated in the file), not
  facts.
- A whole season's passing level is last season's (4,000-yard passers 12.0 a season against 8.2 on 2021-2025,
  when real passing fell every year), and the season leaders' tails run long (passing leader's 95th percentile
  5,665 yards against 5,303, receiving 2,206 against 1,919).
- Real margins land on exactly 3 in 14.6% of games, simulated ones in 11.9% (8.0% without the closing window),
  and records run a little extreme (wins sd 3.23 against 3.12).
- Rookie quarterbacks who become starters during a season: 2.7 against a real 5.0. About half of the real ones
  started because the starter was hurt and most of the rest in a late-season switch; simulated quarterbacks get
  hurt a quarter less often (the injury setting: 0.041 new injuries a game, real 0.056) and healthy benchings run
  at half the real rate (the user's choice), which removes most of those chances.
- Weekly projections move a little less from week to week than real ones (median change QB 1.2 against 1.3, RB 1.05 against 1.13).
- A closing-window or overtime TD is credited with its play's mean yards on top of the draw's yards (which are
  scaled down by the window's mean TD yards, so a team's total stays level).

**Commands** (all read-only on the database; the fits rewrite the JSON files the simulator reads;
`sim_next_year_fit` and `sim_eval`'s full mode need nflverse draft picks, downloaded once into
`backend/data/cache/draft_picks.json` and again only with `--refresh` or when a needed season is missing):

```bash
python -m services.sim_eval [--seasons 2021-2025] [--seeds 3] [--mode mid|full|both] [--out report.json]
                            [--workers N] [--refresh]   # realism check: simulated vs real seasons; each
                                                     # (mode, season) in its own process, --workers 1 = serial
python -m services.sim_eval --set talent.role_sd=0.4 --save-tuned   # try a value; save it as tuned
python -m services.sim_eval --fit [--seasons 2021-2025]             # refit the game model (sim_params.json)
python -m services.sim_injuries --fit --seasons 2018-2025 [--check] # refit injuries (sim_injuries.json)
python -m services.sim_next_year_fit --fit [--seasons 2019-2025]    # refit the offseason + rookie premium (sim_next_year.json)
python -m services.sim_depth --fit --seasons 2018-2025              # refit split backfields (sim_depth.json)
python -m services.sim_closing_fit --fit [--seasons 2018-2025] [--window 600] [--refresh]   # closing minutes,
                                                     # overtime, run / pass by the line (sim_closing.json; play-by-play cached)
python -m services.sim_calendar --fit [--seasons 2018-2025]         # pass / run mix by week (sim_calendar.json)
python -m services.sim_carryover --check --seasons 2018-2024        # carried injuries vs real cross-season returns
```

`sim_eval`'s **mid** mode takes a real season as known before week 12, simulates the rest and compares it
with what really happened (weekly point ranges, projection error, QB-receiver correlation, players sitting
out, K and D/ST points); **full** builds a season exactly like the future one, from the season before with
that year's real rookies, moves and schedule, and compares the whole season (preseason rank against final
rank, breakouts, busts, games missed, how its ADP agrees with the real ADP, overall and for the rookies, and
how its preseason expert ranks agree with its ADP). Both modes end with an injury table: for the top-150
ADP skill players, new injuries per game played (he played and missed his team's next game with any injury
tag: a real player ruled out on game day often keeps his Q or D, while a simulated one is always O or IR, so
counting only O/IR would undercount the real rate by about a third) and the share of team games missed,
with the real value, the target and the simulated one. Both modes report the starting quarterbacks' completion %,
attempts a start and interceptions a start (`sim_metrics.passing`). Both modes also report whole games
(`sim_metrics_games`: the pregame line against the result, a real season's Vegas line and a simulated one's own,
the spread of margins, blowouts of 21+, 28+ and 35+, blowouts in the most lopsided and the closest fifth of
games, home wins, ties, team scores and how often a rare score such as 5 shows up) and the expert ranks
(week-to-week churn of healthy players and agreement with the projections, by position).

```bash
python -m services.sim_lines_fit [--fit] [--seasons 2018-2025] [--refresh]   # the pregame line model (sim_lines.json)
python -m services.replay_fillin --fit [--seasons 2019-2025] [--probation 5]  # fill-in QB start shares (replay_fillin.json)
python -m services.sim_qb_fillins --real 2019-2025                            # fill-in QBs in real seasons
python -m services.sim_qb_fillins --sim 2027 [--seeds 200] [--set market.fill_in_trust=0.3]   # ... and simulated ones
```

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
| GET | `/api/rankings?position=` | Rest-of-season rankings with trade value, schedule factor, and weekly ECR |
| GET | `/api/rankings/weekly?week=&position=` | Week X projections with every source's number and the matchup. For the current week, refreshes stale outside sources first unless `refresh=false` |
| POST | `/api/rankings/weekly/refresh?week=&force=` | Refresh outside sources (`force` re-fetches Yahoo and ESPN; props keep their budget); per-source status |
| GET | `/api/rankings/weekly?ids=1,2,3` | Only these players (start/sit). Every row has `projection`, `if_active`, `play_prob`, `status`, `floor`, `ceiling`, `scenarios`, `flags` |
| GET | `/api/rankings/weekly/{id}/explain?week=` | Step-by-step explanation of one projection |
| GET | `/api/insights/flags?kind=breakout\|regression\|risk` | Flagged players this week |
| GET | `/api/waivers?max_rostered=&position=` | Waiver targets with score, FAAB bid, reasons |
| POST | `/api/trade/find` | Body `{"my_players": [ids], "want_position": "RB"}`; acceptable trade offers |
| GET | `/api/schedule-strength?position=&week=` | Defenses by opponent-adjusted points allowed, plus each team's upcoming schedule |
| GET | `/api/leaderboards/metrics` | The metric catalog |
| GET | `/api/leaderboards?category=&metric=` | Advanced-stat leaderboard (`season`, `min_volume`, `position`, `ascending`, `limit`) |
| GET | `/api/experts/rankings` | FantasyPros ECR (ROS via API key if set, else the free weekly snapshot) |
| POST | `/api/trade/evaluate` | Body `{"give": [ids], "get": [ids], "settings": {...}, "league_id": 1}` (league optional); values, verdict, market view, balance suggestions, and with a league both lineups' gains (`lineup`) |
| GET | `/api/insights/buy-low` | Buy-low candidates with reasons |
| GET | `/api/insights/sell-high` | Sell-high candidates with reasons |
| GET | `/api/insights/trending?type=add\|drop` | Sleeper trending players |
| GET | `/api/integrations/status` | Which integrations are configured and connected |
| GET | `/api/integrations/yahoo/auth-url` | Yahoo sign-in URL |
| POST | `/api/integrations/yahoo/exchange` | Body `{"code": "<code or full redirect URL>"}` |
| GET | `/api/integrations/yahoo/leagues` | Your Yahoo NFL leagues |
| POST | `/api/integrations/yahoo/leagues/{key}/import` | Import teams and rosters |
| GET | `/api/my-team/leagues` | Your imported leagues (your team, scoring, starter slots) |
| GET | `/api/my-team/{league_id}` | Your roster with projections and the best-lineup advice |
| GET | `/api/my-team/{league_id}/waivers?position=` | Waiver targets on no roster in that league |
| GET | `/api/my-team/{league_id}/trades?want_position=` | Trade offers with each real team, scored by both lineups (named drop/add, `helps_both`, `lineup_label`, `accept_chance`); empty after the deadline |
| GET | `/api/my-team/{league_id}/matchup` | This week's opponent, both lineups, win probability, expected/upside/safe lineups, the updated lineup and the bench |
| GET | `/api/my-team/{league_id}/playoffs` | Each player's playoff weeks (opponent, bye, projection), the best lineup per week, and planner fixes |
| GET | `/api/my-team/{league_id}/streaming` | DEF (and one-QB-league QB) streaming grid for 3 weeks with a suggested path |
| GET | `/api/my-team/{league_id}/league` | Power rankings, simulated playoff odds, all-play records and luck, manager tendencies and tags, and graded past trades (live from Yahoo, cached 1 h) |
| GET | `/api/my-team/{league_id}/news?limit=` | Newest news across your roster, plus `latest` (each player's newest item time) |
| GET | `/api/players/{id}/news` | One player's latest news (Sleeper) |
| GET | `/api/players/{id}/usage?season=&scoring=` | Position percentiles for key season stats (with sample sizes) and last-3-games usage changes |
| GET | `/api/odds?season=&week=` | A week's games with spread, total, implied team totals, and the stored player props per game (never calls The Odds API) |
| GET | `/api/compare?ids=&seasons=&scoring=` | 2–4 players' games and season summaries across up to four seasons |
| GET | `/api/rankings/weekly/kdst?position=K\|DST&week=` | Kicker or defense projections with their inputs |
| GET | `/api/model/accuracy?scoring=` | Live accuracy, floor/ceiling coverage, held-out backtest, learned weights |
| GET | `/api/system/status` | Auto-update last run and result, Odds API credits left, last backup, data freshness |
| POST | `/api/system/backup` | Back up the database now |
| POST | `/api/my-team/{league_id}/sync` | Re-import that league from Yahoo now |
| GET | `/api/agent/tools` | Tool definitions (JSON Schema) |
| POST | `/api/agent/tools/{name}` | Body `{"arguments": {...}}`; run one tool |
| POST | `/api/agent/chat` | Body `{"messages": [{"role": "user", "content": "..."}]}`; answer plus tool trace |
| GET | `/api/rankings/rushing` | Original rushing endpoint (kept for compatibility; now uses the fixed leaderboard math) |
| GET | `/api/replay/seasons` | Which past seasons can be replayed (readiness per season, with its `last_week`), the simulated ones (`simulated`: the live and future season, `through`, `ready`, `can_start_now`, `reason`) and the running data import |
| POST | `/api/replay/seasons/{season}/prepare?force=` | Import that season's results, ADP, pre-game projections and injury tags in the background |
| GET | `/api/replay/games` | Saved replay leagues, most recently played first |
| POST | `/api/replay/games` | Body `{"season": 2019, "teams": 12, "scoring": "half", "draft_slot": 6, "waiver_mode": "wednesday", "difficulty": "normal", "playoff_end_week": 17, "playoff_teams": 6, "name": "...", "seed": 1}` (only `season` is required; it may be `"random"`; teams 4/6/8/10/12/14/16; `playoff_end_week` 16/17/18 (default 17; 422 when the season is shorter; a random season is drawn from the ready seasons that long), `playoff_teams` 4-8 (default by league size), the regular season is what the playoff rounds leave before the final; `waiver_mode` `"wednesday"`, `"faab"` (blind bids from $100) or `"none"` (free agency only); `difficulty` `"hard"`; `start` `"full"` (default) or `"now"`, the live season only: draft today and play from the next week); `season` may also be the live or future season, whose simulated season is built and saved with the league; a new league, drafted up to your first pick |
| GET | `/api/replay/games/{id}` | Game summary: phase, week, your record and matchup, what to do next, champion |
| POST | `/api/replay/games/{id}/delete` | Delete the league |
| GET | `/api/replay/games/{id}/draft?position=&search=&sort=&direction=&limit=&offset=` | Draft room: picks, order, your roster, players left by preseason rank (`sort` `rank`, `adp` or `projection`; `direction` `asc`/`desc`, default asc, desc for projection; unknowns last), each with the round his rank implies |
| POST | `/api/replay/games/{id}/draft/pick` | Body `{"player_id": 1, "overall": 6}`; your pick, then the CPUs pick up to your next turn. `overall` is optional: the pick number the page shows; 409 if the draft is at another pick, so a double click never picks twice |
| POST | `/api/replay/games/{id}/draft/auto?whole_draft=&overall=` | Auto-pick for you: this pick, or every pick left (`overall` as above) |
| GET | `/api/replay/games/{id}/team?team_id=&week=` | Any team's lineup and bench for a week up to the current one, with projections, values and (once played) real points |
| POST | `/api/replay/games/{id}/lineup` | Body `{"starters": {"QB": [id], ...}}`, or `{"starters": null}` for the best lineup |
| GET | `/api/replay/games/{id}/matchup?week=&team_id=` | One game's two lineups side by side |
| GET | `/api/replay/games/{id}/scoreboard?week=` | Every game of a week |
| GET | `/api/replay/games/{id}/standings` | The table with seeds, the playoff weeks and byes (the bracket: `.../playoffs`) |
| GET | `/api/replay/games/{id}/playoffs` | The bracket for the Playoffs page: every title round (open slots until decided, round one projected from today's seeds), byes, and the consolation pools week by week |
| GET | `/api/replay/games/{id}/schedule?team_id=` | A team's opponents and results |
| GET | `/api/replay/games/{id}/activity?kind=&limit=&offset=` | The league log (picks, moves, trades, results), newest first |
| GET | `/api/replay/games/{id}/players?position=&search=&status=&sort=&dir=&stats=&stat_week=&limit=&offset=` | The player pool (default: unrostered) with status, projection, value, points so far, usage over his last 3 games, the opponent's matchup rating and a stat line (`stats` `season`, `last4` or `week` with `stat_week` 1..current); `sort` any column (`value`, `projection`, `ros_ppg`, `adp`, `points`, `name`, `ppg`, `last`, `line_points`, `line_projection` or a box key such as `rec_yds`), `dir` `asc`/`desc`; 422 on a bad value |
| GET | `/api/replay/games/{id}/players/{player_id}` | One player's weeks so far (projection, real points, box line, opponent and its rating, usage, value by week; from the week he entered the pool), the weeks to come with an estimate and rating, what the ratings rest on, and who owns him |
| GET | `/api/replay/games/{id}/box?week=&team=` | A played NFL game's box score (either team names it): both teams' final scores (null in a league saved before scores were kept) and every pool player's line and fantasy points; 422 for an unplayed week or a team without a game |
| GET | `/api/replay/games/{id}/claims` | Your pending waiver claims in order (with their bids), the waiver priority, and in a FAAB league every team's budget left |
| POST | `/api/replay/games/{id}/claims` | Body `{"add": id, "drop": id, "bid": 12}`; a waiver claim (before the week's waiver run). `bid` (whole dollars, 0 to your budget left) is required in a FAAB league and refused otherwise |
| POST | `/api/replay/games/{id}/claims/{claim_id}/bid` | Body `{"bid": 20}`; change a pending claim's bid (FAAB, before the waiver run) |
| POST | `/api/replay/games/{id}/claims/{claim_id}/cancel` | Cancel a claim |
| POST | `/api/replay/games/{id}/claims/reorder` | Body `{"ids": [claim ids]}`; your claims in a new order |
| POST | `/api/replay/games/{id}/add-drop` | Body `{"add": id, "drop": id}`; add a free agent (after the waiver run) |
| POST | `/api/replay/games/{id}/drop` | Body `{"player_id": id}` (also drops a player from IR) |
| POST | `/api/replay/games/{id}/ir/place` | Body `{"player_id": id}`; move a player tagged O, IR or PUP this week to your IR slot |
| POST | `/api/replay/games/{id}/ir/activate` | Body `{"player_id": id, "drop": id, "swap": id}` (`drop` and `swap` optional, not both); bring him back, dropping `drop` when the roster is full or moving the injured `swap` to IR in his place |
| POST | `/api/replay/games/{id}/advance` | Body `{"step": "wednesday", "week": 3}` runs the waivers, `"play"` plays the week (409 if `week` is not the league's current week) |
| | | The lineup, claim, add-drop, drop, IR, propose and counter bodies take the same optional `"week"` (the page sends it): 409 when the league has moved on, so a move made on an old page never lands in the next week. Once the season is over every move is a 422 "This season is over." |
| GET | `/api/replay/games/{id}/trades` | CPU offers waiting for you (with a preview verdict), every past offer and trade (`counter_to` marks a counter), and whether trades are open |
| POST | `/api/replay/games/{id}/trades/evaluate` | Body `{"to_team": 3, "give": [ids], "get": [ids], "drops": [ids]}`; that CPU's verdict with reasons (nothing changes) |
| POST | `/api/replay/games/{id}/trades/propose` | Same body; the trade is carried out when the CPU accepts; a decline carries the CPU's one-line `reply` and may come back with its `counter` offer |
| POST | `/api/replay/games/{id}/trades/offers/{offer_id}/respond` | Body `{"accept": true, "drops": [ids]}`; answer a CPU's offer (declining a haggler's opening offer returns his `counter`) |
| POST | `/api/replay/games/{id}/trades/offers/{offer_id}/counter` | Body `{"give": [ids], "get": [ids], "drops": [ids]}`; counter a CPU's offer with your own deal (answers like propose) |

Every replay `POST` under `/games` answers `{"game": <summary>, "events": [...]}` plus the action's own
result (a declined trade is a 200 with its verdict). A replay league or player that does not exist is
404, bad input 422, a season whose data is not imported 503.

Full TypeScript types for every response: [`docs/API.md`](docs/API.md).

## AI assistant and agent tools

With a Yahoo league imported, more tools answer from your real league:
- `get_my_team`: your roster, projections and lineup advice;
- `get_my_matchup`: this week's opponent, both lineups, win probability and the changes to make;
- `get_league_rosters`: any team's roster, or every team's top players and weak positions;
- `get_playoff_outlook`: your players across your league's playoff weeks, with planner fixes;
- `get_power_rankings`: every team's strength, record, playoff odds, all-play record and luck, manager tendencies and tags, and graded past trades;
- `get_player_news`: a player's latest headlines (any player, league or not);
- `get_league_waivers`: players free in your league, each with the action, FAAB bid, market
  price, value to you, the drop (and why), and only players who actually improve your lineup;
- `find_league_trades`: offers with real teams (optionally only for players you name).

Two decision tools answer the most common questions in one call:
- `start_sit` (2–4 players): projection, range, chance to play, matchup, Vegas total, kickoff
  and lock status, plus a verdict built in code: toss-up (under 1.5 points apart), lean (under 3)
  or clear, and how often one outscores the other in 10,000 correlated simulations (fixed seed).
  A player whose game has started is never the pick.
- `get_injury_report` (a player, an NFL team, or your roster): status, injury, practice, chance to
  play, when inactives come out, and **who inherits the work** if he sits. Ruled-out players get a
  row instead of an error.

The assistant's instructions include each imported league with **its own scoring, team count
and superflex setting**, your current roster, today's date, and "lineups are for week N; stats
through week M", so it never asks which players you have and doesn't mix PPR and standard
numbers. The general tools (`get_weekly_rankings`, `get_fantasy_rankings`, `explain_projection`,
`compare_players`, `evaluate_trade`, `get_player_profile`) take an optional `league` and then use
that league's scoring; every ranking result says which scoring and week it is. `evaluate_trade` in
a league also scores both real lineups (`your_lineup_gain`, `their_lineup_gain`) and only suggests
balancing players from the two rosters involved. Players to receive from different teams ("Bijan
or Walker?") come back as one trade per team instead of an error, and `find_league_trades` says
why when it finds no offer. Pick a league above the chat box (shown with 2+
leagues) to make it the default.

**Tool results always fit and stay valid JSON** (`tools/shaping.py`): tools return compact rows
(about 230 characters per weekly row instead of 800), and anything still over the provider's
budget loses rows from the end with a `"truncated": {"shown", "total", "hint"}` note, never a
cut in the middle of the JSON. Earlier rounds' results shrink to short digests when a request
would exceed the provider's per-request budget; if it is still over, earlier chat replies shrink
to 600 characters and then the oldest messages drop (the latest question and remembered facts
always go).

**Intent routing** (`services/agent_routing.py`): each question gets only the tools it's likely
to need, at most 10, with each chosen topic's two main tools ahead of every topic's extras (so
"find trades with a good playoff schedule" keeps `find_league_trades`). Intents are scored by weighted keywords, follow-ups carry the previous topic
and players, and greetings get almost no tools. Player names are found by
`services/player_index.py`: punctuation-insensitive full names ("AJ Brown"), about 40 nicknames
("CMC", "JSN", "ARSB"), unique last names of active players, and typos ("Gibs" → Jahmyr Gibbs).
An ambiguous name is flagged with its candidates, never guessed; a lone partial match on a
retired player is no longer silently returned.

**Chat memory:** when you state your record, goals or players you won't trade, the assistant
calls `remember`. Those facts show under "Keeping in mind" and go back with every message,
so they survive even though only the last 20 messages are sent. Facts sent back by the browser
are filtered again (length, printable, no instruction-like text). The chat, memory and league
choice are saved in this browser (localStorage) and survive a reload; New chat clears them.

**Chat page:** starter questions are built from your leagues and the week, follow-up chips come
from fixed templates keyed on the tools used (never model or Yahoo text), a "Sources" list shows
which data was used and for which week, a note appears when an answer was partial or had
unverified details, and a free-tier rate limit shows a countdown with Retry.

**Instant answers without AI** (`services/agent_direct.py`): a few plain questions are answered
straight from a tool result with a fixed template and no LLM call, so they cost no free-tier
quota and work without a provider key: "show my roster" / "who's on my team" (`get_my_team`),
"who should I start" / "set my lineup" (best lineup, changes and alerts from `get_my_team`),
"standings" (by record, then points for) and "power rankings" (`get_power_rankings`). The whole
question must be one of those phrases (no players, conditions or second requests) and one league
must be in scope (the chat's league or the only one imported). A reply to a question the
assistant just asked ("your team or Rival's?" -> "my team") goes to the AI, as does anything else
or a tool error; if Yahoo itself is down, the app says so at once instead of letting the AI wait
through the same timeout again. The reply has the normal shape plus `answered_by: "app"`, and the chat
shows "Answered instantly from the app's data, without AI."

**Evaluation set** (`services/eval_cases.py`, runner `services/agent_eval.py`): 69 saved
questions across 18 intents, including multi-turn follow-ups and out-of-scope questions; 25 are
held out for scoring only. The test suite checks offline that routing sends every case's tools,
plus the loop, grounding, truncation, fallback and injection cases with a scripted fake model.
With a provider key, `python -m services.agent_eval` runs them through the real model and checks
tools (required, allowed and forbidden), grounded players and numbers, that a decision question
gets a decision, banned phrases, and that it never asks for your roster. It writes a JSON report
(git SHA, prompt and schema hashes, tokens, rounds) to `backend/eval_runs/`; `--compare a.json
b.json` shows the changes and fails on a holdout regression, `--provider`, `--split`, `--repeat`
and `--resume` (after a daily limit) are available. Live runs are manual, never in CI.

The same general tools are exposed three ways: `search_players`, `get_player_profile`,
`get_player_game_log`, `compare_players`, `get_fantasy_rankings`,
`list_leaderboard_metrics`, `get_leaderboard`, `evaluate_trade`, `get_buy_low_candidates`,
`get_sell_high_candidates`, `get_weekly_rankings`, `get_schedule_strength`, `explain_projection`,
`get_waiver_targets`, `find_trades`,
`get_expert_rankings`, and `get_trending_players`. The weekly tools read the stored
snapshot; an agent never triggers outside fetches.
Arguments are validated against each tool's JSON Schema, and players can be passed by name
(typo-tolerant; ambiguous names like "Mike Williams" return the candidates) or by id.
If a tool fails unexpectedly, the error is logged on the server and the model only sees a
generic message.

**Grounding** (`services/grounding.py`): before a chat answer is shown, its player names (full
names and unique last names) and its numbers (decimals, percents, FAAB dollars, "N points") are
checked against the evidence: tool results, your messages, the league rosters in the
instructions, and earlier answers. Numbers match within rounding, as a percent of a fraction
(58% = 0.58), or as a gap or total of two numbers the answer already backs up. If anything is
unverified, the model gets one combined round to fix it or look it up; if it still can't, the
answer ships with a "Not verified against the app's data: …" line and the chat shows a note.
The out-of-rounds answer is checked the same way. Tool fields say what they are (`proj`,
matchups and schedules in words like "6% harder than average") so the model can't mislabel a
projection as an actual score.

**Roster check** (`services/grounding_roster.py`): when one league is in scope (the chat's
league, the only imported one, or the one league this question's tools used), advice is checked
against every team's imported roster. "Pick up / add / claim / bid on X" is flagged when X is
already on a roster in that league, and "start / bench / drop X" when X isn't on yours. It goes
through the same one revision, then the "Not verified" line. A player the answer adds first, or
receives in a trade priced this turn (`evaluate_trade`'s `get`), counts as yours for a later
start. Only advice-shaped clauses count (the clause starts with the verb, optionally after a list
marker or "I'd", "you should" and the like, and the
player's full or unique last name follows it directly), so "Rival will start X", "don't drop X",
ambiguous last names and typo guesses are never flagged.

**Prompt rules** worth knowing: a downgrade must state the points-per-game change, projections
and simulations are called estimates (only box scores are facts), and the prompt lists what the
app can do (price any trade with `evaluate_trade`, waivers and bids, start/sit, matchup odds,
injuries, rankings, stats, playoff odds and standings) so the model never says it can't.

**Untrusted text:** news headlines and summaries pass through `labels.untrusted()` (one line,
no control or zero-width characters, role markers and instruction-like sentences replaced
with "[text removed]"), and the instructions say such fields are quotes, never instructions.
Every tool except `remember` is read-only, and a test fails if a new tool isn't on that list.
Unexpected tool errors (including library `ValueError` text) reach the model only as a generic
message.

**Free LLM providers** (`services/llm_client.py`, one OpenAI-compatible client on `httpx`):
`LLM_CHAIN` lists providers in order; on a rate limit or outage the next one answers and the
limited one cools down (honoring `Retry-After`). A request that is too large for the provider
returns "ask a narrower question" instead of "wait a minute". Each provider has its own
tool-result and request budgets, and each request is sized for the provider it goes to next.
Gemini tries `gemini-3.8-flash`, then `gemini-3.5-flash`, then `gemini-3.1-flash-lite` when a
model is overloaded (503 "high demand"), failing or out of its own daily quota (on a free key
each model allows about 20 requests a day), and stays on the model that answered. A question
can start on Groq and finish on Gemini: Gemini 3 rejects earlier tool calls without a "thought
signature", so calls from other providers carry Google's documented placeholder. When one
provider is rate-limited and another is down, the chat waits until the rate-limited one is back.
A model only gets its own thought signatures back; calls made by another Gemini model also get
the placeholder. A 400 (an invalid request) is not retried on other models, and only one
timeout per call moves on to the next model.

| Provider | Default model | Free limits (checked 2026-10-30) | Data use |
|---|---|---|---|
| `groq` (default) | `openai/gpt-oss-120b` | 30 requests/min, 1,000/day, 8,000 tokens/min | Not retained by default |
| `gemini` | `gemini-3.8-flash` (then `3.5-flash`, `3.1-flash-lite`) | Not published outside AI Studio; reports range from 20 to 1,500 requests/day | **Free-tier prompts and answers may be used to train Google's models and read by human reviewers.** Prompts include your rosters and league-mates' team names |
| `openrouter` | `google/gemma-4-31b-it:free` | 20 requests/min, 50/day (1,000/day after buying $10 of credits once) | Depends on the upstream provider; many free models require allowing training |
| `ollama` | `gpt-oss:20b` | None (your hardware) | Never leaves your machine |

Gemini and OpenRouter are opt-in for that reason, e.g. `LLM_CHAIN=groq,ollama`. Mistral's free
tier trains on inputs by default, Cerebras is trial credit only, and GitHub Models shut down
on 2026-07-30, so none of them is offered. On Groq, a question that needs tool rounds usually
passes the 8,000 tokens/minute cap on its second request. The chat then waits out the
provider's `Retry-After` (at most 45 s at a time and 90 s per question) and continues, so the
answer comes slower instead of failing; a longer wait (the daily cap) fails at once with a
countdown, and the page names it as the daily limit.

1. **REST:** `GET /api/agent/tools` lists them, `POST /api/agent/tools/{name}` runs one.
2. **Built-in chat:** `POST /api/agent/chat` runs a multi-round tool-calling loop on the
   providers in `LLM_CHAIN` (set `GROQ_API_KEY` for the default). Rate limits return 429 with
   `Retry-After`; the reply includes `stop_reason`, `unverified`, `provider`, `answered_by`
   (`"ai"`, or `"app"` for an instant answer), `data_as_of` and `suggestions`. Try it from the command line:
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
| Yahoo expert rankings | nothing | Weekly positional ranks from Josh Norris, Hayden Winks, and 40+ other Yahoo rankers (cached 3 h) |
| ESPN projections | nothing | Weekly projected stat lines (cached 3 h) |
| The Odds API | `ODDS_API_KEY` (free: 500 credits/month) | Vegas player props, the most heavily weighted Week X source (each game bought once, within 30 h of kickoff: about 112 credits a week) |
| FantasyPros API | `FANTASYPROS_API_KEY` | ROS consensus rankings. Without a key you still get the free weekly ECR snapshot |
| Yahoo Fantasy | `YAHOO_CLIENT_ID`, `YAHOO_CLIENT_SECRET`, `YAHOO_REDIRECT_URI`, `TOKEN_ENCRYPTION_KEY` | Import your leagues, teams, and rosters |

**Yahoo expert rankings and ESPN projections** come from the JSON endpoints behind their
public rankings pages. They're unofficial and can change without notice, and both sites'
terms restrict automated collection, so treat them as personal-use only. The app makes
about 13 small requests per refresh, at most every 3 hours, and keeps only each week's
latest snapshot. After a failed refresh it waits 30 minutes before trying again, and a
response it can't read shows up as an error for that source only. To stop contacting
either one, remove it from `WEEKLY_SOURCES`.

**The Odds API:** sign up at <https://the-odds-api.com> for a free key and add it to
`backend/.env` as `ODDS_API_KEY`. Listing games is free; each game's props cost 7 credits
(7 markets × 1 region). **Each game's props are bought once, within 2 hours of kickoff**
(after Sunday-morning injury news; about 16 × 7 = 112 credits a week). When the month's credits
can't cover every game left, only games with your rostered players are bought, plus every
one-game Thursday and Monday slate. Every purchase is logged in the database, so restarts don't
re-buy anything; each game is stored as soon as it's bought, so a failure partway through
keeps what was paid for. Refreshes stop while fewer than 17 credits remain and back off
30 minutes after a failed call. Re-buying lines that moved is only possible on purpose:
`python -m services.weekly_refresh --force-props`.

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
5. List your leagues and import one. The import brings in the league's rules (scoring from
   the points per reception, starter slots, bench size) and every team's roster.
   - **Player matching:** by Yahoo id through the nflverse crosswalk, or by name + position +
     team when the crosswalk doesn't have a recent rookie yet (the Yahoo id is then saved).
   - **Not modeled:** kickers and defenses are listed as `untracked_players`.
   - **Keeping it current:** while the API runs, imported leagues re-sync every 6 hours.
     The **My Team** page has a **Sync from Yahoo** button for an immediate refresh.

Yahoo requires showing "Fantasy data provided by Yahoo Fantasy" wherever its data appears.

## Configuration

Settings come from environment variables or `backend/.env` (never commit it; see `backend/.env.example`).

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///backend/database/fantasy.db` | Any SQLAlchemy URL |
| `CORS_ORIGINS` | `["http://localhost:5173","http://127.0.0.1:5173"]` | JSON list of allowed web origins |
| `FANTASY_END_WEEK` | `18` | Last fantasy week, the final (drives weeks remaining; playoffs default to 16–18) |
| `ALLOWED_HOSTS` | `["localhost","127.0.0.1"]` | Host headers the API accepts (blocks DNS rebinding) |
| `API_TOKEN` | — | Optional. When set, every `/api` request except `/api/meta` needs `Authorization: Bearer <token>`; enter it once in Settings (stored in that browser). At least 32 characters (the API won't start otherwise; make one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`), and `/docs`, `/redoc` and `/openapi.json` are turned off |
| `BACKUPS` | `true` | Daily SQLite backup while the API runs |
| `BACKUP_DIR` | `backend/database/backups` | Where backups go (git-ignored) |
| `BACKUP_KEEP` | `7` | How many backups to keep |
| `FANTASYPROS_API_KEY` | — | Enables FantasyPros ROS rankings |
| `YAHOO_CLIENT_ID` / `YAHOO_CLIENT_SECRET` | — | Yahoo OAuth app credentials |
| `YAHOO_REDIRECT_URI` | `oob` | Must match the Yahoo app setting |
| `TOKEN_ENCRYPTION_KEY` | — | Fernet key used to encrypt OAuth tokens at rest |
| `ODDS_API_KEY` | — | Enables Vegas player props in Week X rankings |
| `WEEKLY_SOURCES` | `["sleeper_status","yahoo_experts","espn","vegas_props"]` | JSON list of outside weekly sources to fetch |
| `WEEKLY_AUTO_REFRESH` | `true` | Let `/api/rankings/weekly` refresh the current week's stale sources before answering (never for cross-site requests) |
| `AUTO_UPDATE` | `true` | While the API runs, load each finished week's stats (updating the learned weights) and save the current week's pre-game projections, checked every 6 hours |
| `LLM_CHAIN` | `groq` | Comma-separated free providers for the AI chat, tried in order (`groq`, `gemini`, `openrouter`, `ollama`). Providers without a key are skipped |
| `GROQ_API_KEY` | — | Enables Groq (the default provider) |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Groq model for the chat (free tier). `openai/gpt-oss-20b` is faster but invents players far more often |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | — / `gemini-3.8-flash` | Optional Google Gemini free tier (see the training caveat under AI assistant) |
| `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` | — / `google/gemma-4-31b-it:free` | Optional OpenRouter free model |
| `OLLAMA_MODEL` / `OLLAMA_BASE_URL` | `gpt-oss:20b` / `http://localhost:11434/v1` | Optional local model through Ollama (no key; nothing leaves your machine) |
| `AGENT_SHARE_PFR_DATA` | `false` | Include PFR-sourced snap counts in AI tool results |

## Data sources

All the core data is free and needs no key, via [`nflreadpy`](https://github.com/nflverse/nflreadpy).
[`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) covers every source the app uses or could use:
what it has, how to access it, license terms, update schedule, and which ones to avoid
(KeepTradeCut, FantasyCalc for calculators, Sports-Reference scraping).

## Safety and operations

- **Optional API token.** By default the API trusts localhost (Host and Origin checks block
  DNS rebinding and cross-site writes). If it's ever reachable from elsewhere, set `API_TOKEN`;
  the token is compared in constant time, and a 401 still carries CORS headers so the web app
  can ask for it. The MCP server calls the tools directly and isn't affected.
- **Backups.** Projection history and learned weights can't be re-downloaded, so the
  auto-update copies the database once a day with SQLite's online backup API (consistent while
  the app runs) and keeps the newest 7. `python -m services.backup` or **Back up now** makes
  one immediately. Restore by stopping the API and copying a backup over
  `backend/database/fantasy.db`.
- **Job status.** The auto-update records each check (what it loaded, synced and saved, or the
  error type) in the `app_state` table; props purchases record the credits left. Errors are
  stored by type only, since messages can contain URLs with API keys. Each step (stats load,
  Yahoo re-sync, pre-game snapshot) runs even if an earlier one failed, and the check is marked
  failed with a note when a step fails, a week's stats didn't actually arrive, or a Yahoo league
  couldn't be re-synced. It works on the season by date, so a new season's schedule and weeks
  load in September without running `populate_stats` by hand.
- **Cross-site and remote requests.** Browsers label requests another site makes, so any
  `/api` request marked cross-site is refused, GETs included (several call Yahoo or run
  simulations). A page on another localhost port counts as "same-site" to the browser, so those
  requests are refused too unless their `Origin` is in `CORS_ORIGINS` (the app's own `fetch()`
  always sends it), so a frontend served from anywhere else (e.g. `vite preview` on :4173) must
  be added to `CORS_ORIGINS`. Without `API_TOKEN`, only this machine may call the API.
- **Signed snapshots.** Cached rankings are pickled, so each is signed with a key kept in
  `backend/.snapshot_key` (git-ignored); a snapshot that doesn't verify is ignored. The key is
  written to a temp file first and must be 32 bytes, so a failed write can't leave an empty key.
- **Point-in-time history.** A player's pre-game projection is saved until his game kicks off,
  never after, so a rebuild once news is out (an in-game injury) can't rewrite it with hindsight.
- **Assistant memory** only keeps facts that restate the user's own words, so text in a news
  headline or team name can't plant a lasting instruction.
- **No leaked keys.** The Odds API needs its key in the URL; a test makes failed requests and
  checks that neither the error messages nor the logs contain it.
- **One app player per Yahoo player.** A unique index on `players.yahoo_player_id` (migration
  `e6a8c0d2f4b6`, which refuses to run if duplicates exist), and the nflverse crosswalk no
  longer reassigns a Yahoo id that another player already holds.

## Testing

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest                                   # all tests run on an in-memory database
python -m pytest --cov=. --cov-report=term-missing # with coverage
```

GitHub Actions ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs the suite on every pull request.
Tests never touch `database/fantasy.db` or read `backend/.env`. All HTTP calls to
Sleeper, FantasyPros, Yahoo, ESPN, The Odds API, and Groq are mocked.

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
  api/                      thin routers: data, market, integrations, agent, legacy, replay (+ replay actions)
  services/                 tested business logic (scoring, projections, valuation, schedule strength,
                            weekly model and rankings, props math, leaderboards, players, trade, insights, agent)
    replay_*.py             Replay League: pool, values and state (data), draft, season, waivers, trades
                            (engine), game (week flow), views (JSON), data (season import CLI),
                            blob (a simulated league's frozen season), live (which seasons are complete,
                            in progress or next), readiness (what is loaded for a season)
    sim_*.py                simulated seasons: setup (the only DB read), season, world (belief), talent
                            (truth), game + draw, market, injuries, next year, schedule, offseason, eval + fits
  sim_data/                 offseason_<season>.json: hand-edited rookies and moves for a simulated season
  integrations/             sleeper, fantasypros, yahoo, yahoo experts, espn, the odds api, token encryption,
                            past-season ADP and projections (adp_history, espn_history, sleeper_projections)
  tools/                    agent tool registry and definitions
  mcp_server.py             MCP server over the same tools
  database/                 models, migrations helpers, data loader
  alembic/                  schema migrations
  tests/                    pytest suite
frontend/                   React + TypeScript + Vite web app
  src/pages/replay/         Replay League: home and setup, the league hub, one view per tab, the draft room
docs/                       plan, data sources, API contract
```

## Design notes

- **Look and feel:** an editorial sports style (near-black and charcoal, brick-red accent, Kanit italic
  headings, Mulish body text from Google Fonts), with a matching light theme. Charts are plain SVG; no chart library.
- **Player identity crosswalk:** Yahoo, Sleeper, FantasyPros, PFR, ESPN, and nflverse each use
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
injury-aware projections, multi-user auth, deployment.
Research work: [`FutureWork.md`](FutureWork.md) (RB Vision Score) and [`DATA_AUDIT.md`](DATA_AUDIT.md) (Big Data Bowl audit).
