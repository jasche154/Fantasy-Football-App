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
  Players carry Yahoo-style injury tags (Q, D, O, IR, PUP, SUS) from that week's official report and roster
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
| **Replay League** | A game: replay a past season against CPU managers. **Start:** under **New league** pick what to play (a past season, or a simulated one: the live season with **Full season** or **Jump in now**, or next season), then a season (or Random), 4–16 teams, the **league format** (Standard, Superflex or Double flex as a start, then 0–3 starters per slot: QB, RB, WR, TE, FLEX, SFLX (superflex: a QB, RB, WR or TE), K, DST; at least one QB or SFLX, at most 12 starters; in superflex the CPUs value and draft quarterbacks much earlier), the **scoring** (PPR, half or standard as a base, then points per reception 0–1.5, a TE premium of +0.5 or +1 per catch, 4 or 6 points per passing TD and −1 or −2 per turnover; played games always score exactly, but with custom settings the pre-game projections are estimates converted from the sites' PPR and standard numbers), your draft slot, the playoff weeks (14–16, 15–17 by default, or 16–18 when the season has a week 18) and how many teams make the playoffs (4–8, default 4 under 10 teams, 6 under 14, else 8), waivers (Wednesday waivers; FAAB bidding: $100 per team, blind bids, the highest bid wins, ties go by waiver priority, winning bids show in the activity log; or free agency only) and difficulty, then **Create league and draft**. Saved leagues are listed above the form (Resume, Delete). **Draft:** the board lists who is left by **Rank** (the preseason expert rank, or the ADP order for 2018–2019, as Yahoo shows a rank next to ADP), with the round that rank implies (**Rd**), ADP and the preseason projection (points a game); sort by Rank, ADP or Proj. Press **Draft** next to a player on your turn, or auto-pick one pick or the rest; the CPUs pick in between. **The week loop:** the one primary button at the top says what comes next. **Play week N** scores the week with the real results; then every unrostered player is on waivers, you put in claims on the Players tab (with a bid in a FAAB league), and **Sim to Wednesday** settles them (by priority, or by bid in FAAB) and lets the CPUs move; then lineups, free-agent adds and trades are open again until you play the next week. **Tabs:** My Team (set your lineup with Move, drop players, look at any team or past week), Matchup (your game and the week's scoreboard), Players (the waiver wire with each player's usage over his last 3 games, adds and claims), Trades (players CPUs are shopping to you, CPU offers with each manager's message, and a builder that shows the CPU's verdict and mood before you propose) and League (standings, schedule, scoreboard, activity, and the league chat where the CPU managers talk after every week). The regular season runs until the playoff weeks you chose; then the top seeds play a title bracket (top seeds get a bye when it has 5–7 teams) and everyone else plays for the places below. **No hindsight:** nothing on screen comes from a week you have not played. Before a week is played you see only its pre-game projection; points, records and standings stop at the last week played, the My Team and Matchup week pickers end at the current week, and later weeks on the League scoreboard show the pairings only |
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
  priced it in, so only the model's share of the boost is added.
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

### Model adjustments: calibration, EPA matchup, weather
`python -m services.projection_factors --fit` refits these and prints how they do on held-out seasons.
- **What it does:** multiplies the model's number, and only the model's (props and experts
  already price matchups and weather), by `1 + b . x` per position:
  - **Calibration:** the raw model ran 3–11% high depending on position.
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
| 2024 (fit on 2021–23) | 7.71 → **7.55** | 6.05 → **5.85** | 0.526 → **0.529** |
| 2025 (fit on 2021–24) | 7.65 → **7.45** | 6.01 → **5.77** | 0.502 → **0.506** |

Most of the gain is calibration (getting the level right). The matchup and weather terms add
a little to ranking; Vegas totals already price in most of what they know.

**Overfitting check.** Each season is scored by a fit on earlier seasons only. The held-out
gain (−0.19 to −0.21 RMSE) matches the in-sample gain (7.74 → 7.53 on 2021–24), and the
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
- On 2026 (data the fit never saw), 7% of players finished below their floor and 12% above
  their ceiling, against a 10% target. The old ranges, fit only on full-role games, had 31%
  below the floor.

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
   week (the old per-week fit put 0% on the model one week and 69% the next).
3. **Limits:** props never below 15% (their value is late news the history can't show yet),
   every other group at least 5%. Under 300 rows, the starting weights stand.

Through week 7 of 2026 the weights barely move from the start (props 30, experts 35, ESPN 15,
model 20): the sources are close enough that seven weeks can't separate them yet, which is
the honest answer. Yahoo's consensus alone is about as accurate as the blend, so the Accuracy
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
- **FPOE** = actual − expected fantasy points per game
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
  2018-2025; MyFantasyLeague's PPR real drafts fill each list's tail past FFC's last pick). A league drafts
  from its own list (`services/replay_adp_format.py`): a superflex league (a SFLX slot or 2+ QB slots) the
  2-QB list, otherwise its scoring's list, and custom scoring the preset nearest its points per reception
  (0.75 -> PPR, 0.25 -> half). The list is chosen once, at creation, and saved with the league
  (`Settings.adp_format`; leagues saved before there were several lists keep PPR), so a league never
  switches lists mid-season. A season missing that list uses the nearest one stored (2-QB -> the
  scoring's list; PPR <-> half <-> standard), and a superflex league without a 2-QB list falls back to
  scaling QB ADP (`replay_chart.SUPERFLEX_QB_ADP`). A re-run of Prepare fetches only the formats a season
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
- **Injuries and IR:** Q, D and O come from the official injury report; IR, PUP and SUS from the weekly
  roster status (PUP = status PUP or reserve code R04; suspended = SUS or R40/R33/R30; every other
  reserve list = IR, except the lists that are not an injury (retired R02, did not report R03, left squad
  R06, the 2020 opt-outs R62), which get no tag, like holdouts; the game-day inactive list is not used).
  A report from a club that is not on the player's roster rows that week is ignored (a player traded
  after his game). An O, IR or PUP tag without text takes last week's injury text unless that text names
  no injury (rest, personal, "not injury related"). A roster status wins over a report tag, and a hard
  tag is carried over the player's bye. O, IR, PUP and SUS mean "out" this week even if a stale
  projection exists; Q and D are information only.
  IR rules: one slot per team; a player is eligible by his tag only (O, IR or PUP: not a suspension,
  not just "no projection"); he does not count against the roster, can't start and can't be traded; he
  can swap places with another eligible player; a team whose IR player lost his tag can't add, claim or
  trade until it activates (dropping someone if full) or drops him, and a CPU in that state declines every
  trade. CPUs stash an injured player worth keeping, swap or cut to bring one back as soon as a week's
  tags are known (when the waiver phase opens, again at the waiver run and at the start of the week), and
  still count an IR player for their positional limits and their view of the rest of the season. A
  week's final report and roster status are shown from the start of that week (before its waiver run),
  like its projection. The draft shows no tags: it is held with what the ADP knew, and some week-1
  statuses came later. Week-1 tags appear when the draft ends, and the CPUs make their week-1 moves
  first, as they do every week.
- **Draft:** a snake draft, 15 rounds by default (QB, 2 RB, 2 WR, TE, FLEX, K, DST, 6 bench). CPUs follow
  ADP: each takes the lowest `(ADP + noise) x need x taste` among the best players left, with position
  caps, K and DST in the last rounds, and never reaches further than a round of picks past the current
  pick in rounds 1-4, a round and a half in rounds 5-9, and 25 picks after that (never more than 25).
  "Hard" halves the noise. The draft board (`GET .../draft?sort=rank|adp|projection&direction=`) defaults
  to **Rank** = `pre_rank` (below), with **Rd** = ceil(rank / teams); players without the number sorted on
  come last either way.
- **CPU rosters:** at most 2 QB, 2 TE, 1 K, 1 DST, 6 RB and 7 WR. A CPU takes one extra only while nobody
  it has at that starting slot plays that week, and trims it afterwards. A CPU never drops a top-100
  player (100 in a 12-team league, scaled to the league size: 66 with 8 teams, 116 with 14), and drops an
  injured player it would otherwise keep only in one case: a starting slot has nobody who plays, its IR
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
  (`ros_ppg` nudged by his last three real games), and an injured player is priced by the share of his
  team's remaining games he is expected to play. Two extras keep the model rank from trailing the news
  (`services/replay_role.py`): from week 2 his newest projection counts half its gap to `ros_ppg` (a back
  the site re-rates after he wins the job moves now, not a quarter at a time), and when a teammate at his
  position is expected to miss games, a player with no projection since then (out or on a bye himself,
  e.g. Mike Evans when Chris Godwin went down in 2024) gains his share of the missed work (75% of a back's
  stays with the backs, 60% for receivers, 50% for tight ends, split by rate). Judged out of sample
  (2021-2025, weeks 2-11, against real points from that week on): per-position rank correlation 0.475 ->
  0.478, better in 4 of 5 seasons. A usage-share signal (a back's share of his team's touches) was tried and
  left out: it moved players the wrong way more often than the right one.
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
  It is built from the week he really returned, blurred by seeded noise (off by up to two weeks, a few
  false "out for the season" calls, season-ending injuries first reported as a return weeks out) that
  shrinks as the return nears; it never names a week already reached. This is the one sanctioned use of
  later data (a beat report would know as much). Shown under a tagged player on the Players and Trades
  screens.
- **The week:** Wednesday waivers by rolling priority (CPUs claim when a player adds about a lineup point
  a week), then free agency, then the games: every team's best lineup by projection (yours can be set
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
  CPU also looks at "hot" players: a skill player not out, who played in the last two weeks and whose mean
  over his last two games is at least 6 points over his `ros_ppg`. It values him at a hot rate (half that
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
  that loses it 20% of market value (counted with the same depth discount). Declines come with the reasons. A player on IR can be traded: he takes the receiving team's free IR slot, else a
  roster spot (which may force a drop). A CPU
  sells you an out player only for at least half his healthy chart value. After a deal each starting slot
  the CPU can no longer fill needs a player it can add (unrostered, not out, not on waivers; in the waiver
  phase, when everyone unrostered is on waivers, anyone you haven't claimed), and it adds him
  (`replay_cover.cpu_cover`): right away, or in the waiver phase once waivers clear. Each CPU has a hidden **persona** (balanced, panicky, shark, steady,
  gambler: `services/replay_personas.py`, assigned from the league's seed at creation) that nudges what it
  thinks a player is worth by up to 15% and how often it offers and counters; after 3 weeks a one-line
  tendency hint is shown, never the persona. Panicky and gambler CPUs are also **emotional**: they price a
  hype move at 1.25x its calibrated size (`replay_personas.HYPE`, at most 8 chart points more), both when
  buying and when holding; the rest stay calm. Small on purpose: Javonte Williams' week-2 jump of 15 points
  becomes about 19 to an emotional CPU. **Counters:** a CPU that turns down a deal on value alone may
  answer with a counter offer it would take (`services/replay_counter.py`; one roll and at most one counter
  per CPU per week), shown under Offers for you;
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
  the chart, high only at or above it), so a mood line or message never points the other way from his price. After 3 league weeks each CPU shows a **mood
  line** in the trade builder ("Worried about X. High on Y."). When a CPU is worried at -0.15 or worse about
  one of his 6 most valuable skill players whose last game fell short of his rate (and who nobody could think
  was hurt: the same test as the chat's dud lines below), he may **shop him to you** at the start of a week,
  after the CPUs' IR and free-agency moves
  (chance by persona: panicky 75%, gambler 35%, balanced 25%, steady and shark never; one shop per CPU, at
  most 2 in the league): he sends you a message, the player shows under **Shopping to you** on the Trades
  tab, and for you only he asks a seeded 5-15% less for that player (panicky 9%, gambler 7%, balanced 6%,
  plus more the more worried he is). The discount is applied once, in the CPU's value test
  (`replay_trade_value.tally` via `replay_state.markdowns`), so evaluate, propose, counters and his own offer
  all see it; it never moves the chart, and the re-trade lock stops you flipping the player. While the shop is
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
  settles it); the others post now and then, at most 5 of these after-the-games lines a week: your
  opponent's line to you is always kept, then the rare news (title, clinching, elimination), then the rest in a seeded order,
  and a line that would run past 140 characters with long names gives way to one that fits. A third manager may react to a
  trade (at most 2 a week) and a shopping CPU messages you (at most 2 shops are open at once); these come on
  top of the 5, so a week can show up to 9 lines. Every CPU offer carries a one-line reason in
  his voice (worried about a player, never one who might be hurt; high on yours, short at a position, a
  counter, or a value pitch).
  In a league that started mid-season (or a simulated one) all of this counts the league's own weeks, and
  each line is dated with the real NFL week.
  Lines come from a bank of 1,400+ clean lines in five voices, one per persona, so a manager sounds the same
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
  seed). Games are labelled Quarter-final, Semi-final, Championship, Third place or Consolation; a bye
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
  On a phone the columns stack.
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
  box-score columns follow the position tab (QB passing; RB carries and catches; WR/TE targets and
  receiving; none for K/DST). Each row also carries advanced rates over the same span
  (`services/replay_advanced.py`): snap % and target share (means over his games), targets, carries and
  touches a game, yards per carry / catch / target, catch rate, total TDs, TD rate per touch, passing
  yards a game and TD:INT, each blank when there is nothing to divide by. Click a column header to sort
  on the server (again to flip); players without the number come last either way.
- **Player card:** his status, five stat tiles, a game log of played weeks (opponent, projection, points,
  box line) and the weeks to come: opponent (@ when away), a **matchup chip** and an **Est.**, his
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
  (the real ones, published during the week, are not read), and the ADP is today's market (what a draft held under the
  current public view would look like, below), not August's. Its defaults: 4 playoff teams (the smallest
  bracket, so the longest regular season), the trade deadline two thirds of the way through the weeks
  left, and at least 3 regular-season weeks must be left (otherwise pick a later championship week or
  fewer playoff teams). The form shows the plan ("plays weeks 12-15 (4 weeks), then the playoffs in weeks
  16-17. Trade deadline: week 13."). Weeks before N+1 are history, not league weeks: the schedule,
  scoreboard, standings and week pickers start at N+1, while a player's game log still shows his real
  earlier games. A CPU's tendency hint shows after 3 league weeks.
- **The future season** (2027) is simulated whole, starting from what is known today: the public view of
  every player at the end of the newest season so far (2026 through its last finished week, so it moves as
  2026 is played), carried through an offseason (below). It needs that season prepared first.
- **Every league is frozen:** each new simulated league rolls its own rest of the season from its seed,
  so two leagues on 2026 play out differently, and the whole season (the real weeks too) is saved with the
  league (table `replay_season_data`, `services/replay_blob.py`, compressed versioned JSON). New real data
  (a finished week, an nflverse stat correction, an updated offseason file) reaches every league created
  after it, with no restart or Prepare needed (the season cache is keyed on the data), and never one created
  before it; deleting a league deletes its season. A league whose saved season can't be read (a damaged
  database file, or saved by a newer version of the app) answers with an error saying so instead of its
  screens (the server log records why, never the save's contents); it is still listed and can be deleted.
  If building a new league's season fails unexpectedly, the create answers 500 with the error's type only
  (the full trace is in the server log) and nothing is saved: never a league without its season.
- **How the UI marks it:** the saved-leagues list puts a **Live 2026** or **Simulated** chip after the
  name; the game screen shows "2026 · simulated from week 12" or "2027 · simulated season" under the
  scorebug; on a player's card every simulated week in the game log and the weeks ahead carries a small
  **Sim** mark, with a line saying from which week the season is simulated.

**How a week is simulated** (`services/sim_*.py`). The simulator keeps two pictures of the league: the
**truth** (how good every player and team really is this season) and the **public belief** (what the
games so far say). Only the games (and the buzz, below) read the truth; projections, expert ranks, ADP and every CPU decision
read the belief, so, as in a real season, the public learns the truth only from results.

- **Games are played top-down,** so box scores always add up (`services/sim_game.py`): first each
  team's targets and carries (its volume, a shared game draw for shootouts, the leader running more and the
  trailer passing more), then they are split among the active players by their roles (an injured player's
  work goes to his teammates, a rookie takes some from everyone), then catches, yards and touchdowns per
  player. The quarterback's passing line is the sum of his receivers' lines, so a QB's yards and TDs always
  match his receivers'. If he leaves hurt, his backup throws part of each receiver's catches (and the TDs
  among them). Kickers get an extra point try for each of their team's TDs and field goal tries that rise
  with the team's yards and fall with its TDs (drives that stall); a kicker who leaves hurt keeps only his
  share of the kicks. A defense's sacks and interceptions are the opposing offense's, and its points allowed
  are the opponent's points. Points use the league's own scoring, custom settings included.
- **Injuries** come from what really happened to fantasy-relevant players in 2018-2025
  (`services/sim_injuries.py`, rates in `sim_injuries.json`): by position, the chance a player who plays a
  game gets hurt in it, how many games he then misses (the real lengths, season-enders included), how much
  of the game he finished, the body part, how often he comes back listed Q, and how often a healthy starter
  is listed Q or D and plays anyway (who counts as a starter there comes from the public belief, never the
  truth). A long absence is tagged IR, like the real reports. Defenses are never hurt. A player already out
  when the simulation starts sits a fitting number of further games.
- **Hidden talent:** every player's true role and efficiency differ from his reputation by a random draw,
  widest for rookies and players with few games, smallest for veterans with a full season
  (`services/sim_talent.py`); teams' volume and defenses move the same way. During the season some
  players **break out** (a backup's role grows), some **bust** (a lead player's shrinks) and some starting
  quarterbacks are **benched** (a poor passer more often; in a whole simulated season a team with a promising rookie behind its
  starter usually turns to him before the season is out). These spreads and rates are tuned so a
  simulated season reshuffles like a real one (preseason rank against final rank, breakouts, busts).
- **Buzz:** before the week a breakout, bust or benching starts, a news line is published for 60% of
  them ("Beat reporters say his role is growing; expect more work.", "Reports say his snaps and touches are
  shrinking.", "Reports say his starting job is in danger."), and false lines about other starters are added
  so only about 60% of all lines are true, in the same weeks a change can start (never the first simulated
  week or the last two). It shows as a small **Buzz** chip (named "Buzz: ..." for screen readers and voice control, tooltip "Buzz (a rumor): ...") on player rows
  and the draft board (press it to open his card), and under **News** on his card. It is the only way the truth reaches the screen
  before the games, and it is right only some of the time.
- **Projections, expert ranks and ADP** are computed from the belief each week (`services/sim_market.py`):
  the belief is refitted from the games played so far (recent games count more), a projection is a
  player's expected points in that week's game with the players ruled out removed (so their teammates
  rise), expert ranks are each position ordered by expected value with a little noise (about 8%), and ADP is
  a 12-team draft in the league's scoring by points over replacement; kickers and defenses take the real
  average ADP of the Nth one drafted. Going into a whole simulated season the market also leans on last
  season's points a game, and the **preseason expert ranks** (what the draft room's Rank order comes from)
  follow that ADP with the same ~8% noise, because real August experts and ADP agree closely (rank
  correlation 0.98 over the real top 150 in 2022-2025; the simulated ones now 0.99, from about 0.86 when the
  experts ignored last season).

**2027 specifics:**

- **Aging:** each veteran's role grows or fades by his position's curve for his years in the league (1, 2,
  3, 4-5, 6-8, 9+), big and small roles move partway back toward the usual one, and each team's
  returning players are rescaled so the curve moves work between teammates without changing the team's
  total. Efficiency regresses toward the position norm. All of it is measured from real seasons 2019-2025
  (`sim_next_year.json`). On each team the QB who started the most games last season is the starter again in August.
- **Rookies** get the role a player taken at that pick and position really had as a rookie, and the widest
  talent draw. A rookie QB starts week 1 when his team has no starter, or when his pick makes it likely and
  the incumbent is a below-average passer; otherwise he is next in line. Real drafters pay for a rookie's
  upside, so the market (ADP and the preseason expert ranks, not his role or talent) multiplies his value by
  a premium fitted on the real rookie ADPs of all of 2019-2025 at once, then checked on held-out seasons
  (`rookie_premium` in `sim_next_year.json`; allowed to fall with the pick but never to rise). The fitted
  premium is flat by position, the same at every pick: x1.22 for a running back, x1.10 for a receiver,
  x1.05 for a tight end, none for a quarterback (rookie QBs already go where real ones do).
- **The offseason file** `backend/sim_data/offseason_2027.json` is meant to be edited by hand. `rookies`:
  one entry per player with `name`, `position` (QB/RB/WR/TE/K), `team` (schedule code, e.g. MIA, LA, LV,
  WAS), `pick` (projected overall pick, 1-262) and `college`. `moves`: `name` (as the app spells him),
  `position`, `team` (the new team, or `null` for retired / out of the league) and a `note`. Names must be
  unique across both lists; everyone else stays on his team. A bad entry greys out the 2027 card with the
  file name and the problem; a move whose name matches nobody is skipped (logged as a warning). Saved
  leagues keep the offseason they were built with.
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
- The rookies and moves in the 2027 file are a snapshot of mock drafts and reports (dated in the file), not
  facts.

**Commands** (all read-only on the database; the fits rewrite the JSON files the simulator reads;
`sim_next_year_fit` and `sim_eval`'s full mode download nflverse draft picks):

```bash
python -m services.sim_eval [--seasons 2021-2025] [--seeds 3] [--mode mid|full|both] [--out report.json]
                                                     # realism check: simulated vs real seasons
python -m services.sim_eval --set talent.role_sd=0.4 --save-tuned   # try a value; save it as tuned
python -m services.sim_eval --fit [--seasons 2021-2025]             # refit the game model (sim_params.json)
python -m services.sim_injuries --fit --seasons 2018-2025 [--check] # refit injuries (sim_injuries.json)
python -m services.sim_next_year_fit --fit [--seasons 2019-2025]    # refit the offseason + rookie premium (sim_next_year.json)
```

`sim_eval`'s **mid** mode takes a real season as known before week 12, simulates the rest and compares it
with what really happened (weekly point ranges, projection error, QB-receiver correlation, players sitting
out, K and D/ST points); **full** builds a season exactly like the future one, from the season before with
that year's real rookies, moves and schedule, and compares the whole season (preseason rank against final
rank, breakouts, busts, games missed, how its ADP agrees with the real ADP, overall and for the rookies, and
how its preseason expert ranks agree with its ADP).

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
| GET | `/api/replay/games/{id}/trades` | CPU offers waiting for you (with a preview verdict), every past offer and trade (`counter_to` marks a counter), and whether trades are open |
| POST | `/api/replay/games/{id}/trades/evaluate` | Body `{"to_team": 3, "give": [ids], "get": [ids], "drops": [ids]}`; that CPU's verdict with reasons (nothing changes) |
| POST | `/api/replay/games/{id}/trades/propose` | Same body; the trade is carried out when the CPU accepts; a decline may come back with the CPU's `counter` offer |
| POST | `/api/replay/games/{id}/trades/offers/{offer_id}/respond` | Body `{"accept": true, "drops": [ids]}`; answer a CPU's offer |
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
