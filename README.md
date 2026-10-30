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
  rest-of-season and **playoff (weeks 15–17) strength of schedule**
- **Chart Studio:** charts built automatically from your selection (two metrics → scatter split at
  the medians, one → ranked bars, players → week-by-week trend), five one-click presets, SVG download.
  Hover a point for the player's name; click a scatter point for a card with both values, ranks and quadrant
- **Advanced-stat leaderboards:** 26 whitelisted metrics (RYOE, separation, YAC over
  expected, CPOE, time to throw, target share, EPA per play, and more), volume-weighted the right way
- **Player pages:** season summary, weekly game log, NGS advanced stats, projection
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
  10,000 simulations of Yahoo's remaining schedule, with each manager's habits (active trader,
  waiver regular, quiet; the positions they trade for and pick up).
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
| **My Team** | Your imported Yahoo team (pick the league if you have several). **Lineup:** starters and bench with this week's projection, range, flags and rest-of-season value; the best lineup under your league's slots, with "Start X over Y (+points)" advice and Start/Sit tags. **Waivers:** players on no roster in your league. **Trades:** fair offers with each real team, built from their roster, teams that need what you'd send first. **Playoffs:** each player's projection for every playoff week (your league's weeks from Yahoo, else 16–18), byes marked, the best lineup per week, your weakest week, and the **Playoff planner**: free agents and players on other teams who beat your weakest starter at each position by 3+ playoff points. **League:** power rankings by lineup strength, record, points for, remaining opponents, simulated playoff odds and projected wins, and each manager's tendencies (also shown next to each team's trade offers). **Latest news** under the lineup lists the newest notes on your players. **Sync from Yahoo** re-imports now (the app also re-syncs every 6 hours) |
| **Week X Rankings** | This week's projections by position; pick any week. Each row shows the matchup grade (opponent-adjusted, #1 = easiest), the Vegas implied team total, the blended projection, and every source's number (props, Josh & Hayden, Yahoo, ESPN, model). For finished games, **Actual** shows what each player scored (green beat the projection by 20%+, red missed by 20%+, DNP sat). Click a column header (Proj, Actual, Team total, or any source) to sort by it. **Schedule strength** switches to defenses ranked by adjusted points allowed and each team's upcoming opponents. **Refresh sources** re-pulls Yahoo and ESPN (Vegas props follow their credit budget) |
| **Rankings** | Rest-of-season rankings by position; sort by actual, expected, or projected points, schedule strength, **Playoff SOS** (weeks 15–17), trade value, or ECR |
| **Week X → Start / Sit** | Add up to four players. Each shows its projection, floor–ceiling range, injury status, "if <teammate> sits" scenarios, flags, and a **Why?** breakdown. A tiebreaker (most points / safe / upside) decides close calls |
| **Waiver Wire** | Players rostered in under 10–75% of ESPN leagues, ranked by rest-of-season value, this week's projection, next three matchups, and injury-driven opportunity, with a suggested FAAB bid |
| **Trade Calculator → Trade finder** | Put the players you'd move in "You give", pick a position you want, and get 1-for-1 and 2-for-1 offers that are fair by this app's values *and* by market value (expert consensus, how the other manager will judge it), closest to fair first; **Load in calculator** opens one |
| **Chart Studio** | Click a quick chart (Volume pays, Separators, RYOE leaders, Downfield QBs, Opportunity score) or pick one or two season metrics and a position; the chart type is chosen for you. Add players to highlight them, or add players with no metric for a weekly trend of any stat. Hover any point for a card with the player, team and both values (guide lines run to each axis); click a scatter point for that player's values, ranks among the plotted players, and quadrant. **Download SVG** saves the chart |
| **Compare Players** | Add up to four players and pick seasons (up to four) and a stat: fantasy points, expected points, targets, carries, scrimmage yards, touchdowns or snap %. One chart puts every game on a single timeline ('25 W5 … '26 W7), with an optional 3-game average; the table summarizes each season (games, PPG, expected PPG, best, worst, usage). Links like `#/compare?ids=1,2&seasons=2025,2026` reopen a comparison |
| **Advanced Stats** | Pick a category and metric, season, and minimum volume, then rank every qualified player |
| **Trade Calculator** | Search players into "You give" / "You get". See each side's value, the verdict meter, and suggestions that even it out; the URL is shareable |
| **Buy / Sell** | Full buy-low and sell-high lists with every reason, filtered by position |
| **Player page** | Weekly chart of actual vs expected points, game log, NGS stats, projection, signals |
| **Ask AI** | Ask questions in plain English; each answer shows which tools it used |
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
   - Players on IR, PUP or suspended lose 4 games; Out or Doubtful lose 1. **IR with a torn
     ACL or Achilles is season-ending: no rest-of-season value** (De'Von Achane drops to 0).
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
and the scoreboard for every remaining regular-season week), cached for an hour:
- **Strength** is each team's best QB/RB/WR/TE lineup in rest-of-season points per game.
- **Playoff odds:** each remaining game is simulated 10,000 times, every team scoring
  Normal(strength, 25 points) that week. Wins and points for are added to the real standings;
  the top `playoff_teams` (Yahoo's setting) by wins, then points for, make it.
- **Tendencies:** "Active trader" (2+ trades), "Waiver regular" (1.5× the league's median
  moves), "Quiet" (2 moves or fewer, no trades), plus the positions each manager has traded for
  and picked up.

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
Each player's week projection is a weighted blend of every source that covers them. These
are the starting weights; each completed week adjusts them a little (see **Learning the
weights** below). After week 7 they're 33 / 28 / 18 / 12 / 9.

| Source | Starting weight | How it becomes points |
|---|---|---|
| Vegas player props | 35% | Yardage lines and over/under prices → expected yards; receptions, pass TDs, INTs → Poisson means; anytime-TD price → expected TDs. Scored with your league settings. Missing markets (e.g. an RB's receiving line) are filled from the model |
| Josh Norris & Hayden Winks (Yahoo) | 30% | Their average positional rank for the week, converted to points (below) |
| Matchup model | 15% | `base × defense factor × (Vegas implied team total ÷ team's scoring average)^0.5`, keeping the shape of the player's stat line |
| Yahoo expert consensus | 10% | Consensus of Yahoo's 40+ rankers, converted to points |
| ESPN | 10% | ESPN's projected stat line, scored with your league settings |

- Weights re-balance over the sources a player has (a backup with no props line is
  blended from the rest). Josh Norris doesn't publish every week; when he hasn't, Hayden's
  rank is used alone.
- **Ranks to points:** rank N at a position gets the Nth best numeric projection (props,
  ESPN, and model blended) at that position, so "RB3" means RB3-level points.
- **Extra schedule tilt:** the blended projection is multiplied by `defense factor^0.25`.
  Props and experts already price in the matchup; this deliberately leans further on
  schedule strength, on top of the model using it at full strength.
- Players on bye are skipped. When outside sources have published the week, players none
  of them cover are left out too (usually injured or inactive).
- The starting weights are in `backend/services/weight_learning.py` (`DEFAULT_WEIGHTS`); the
  tilt is in `backend/services/weekly_rankings.py`.

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

**Learning the weights** (`services/weight_learning.py`). Each completed week with
pre-kickoff projections updates the weights once:
1. **Best fit:** take everyone who played and find the weights that would have predicted
   their actual points best that week. The blend is re-balanced over each player's sources,
   as in the rankings.
2. **Small step:** move the current weights only part of the way there. The step is
   `rows / (rows + 3000)` of the gap, so the starting weights count like about eight weeks of
   evidence. No source moves more than 3 points in a week, and none drops below 5%.

One week's best fit is usually extreme (week 7's put 69% on the model and 0% on props). The
small step keeps one odd week from flipping the blend: a source has to beat the others week
after week to gain real weight.

A source missing from a week (no props for a backfilled week, no Josh & Hayden ranks) is
left out of that week's fit and keeps its weight: missing data says nothing about accuracy.

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

Through week 7 the weights are props 33%, Josh & Hayden 30%, Yahoo consensus 14%, model 13%
and ESPN 10%. Yahoo consensus gained the most; the model lost a little early (it has few
games to go on) and won some back in week 7. Run it again with
`python -m services.history_backfill --season 2026 --weeks 1-6` (`--no-fetch` to rebuild
from the sources already stored).
- **Running it:** it updates on its own every week while the API runs: the background
  check loads the finished week and the load runs the update. The check also saves the next
  week's pre-game projections, so the learner has data even in weeks you never open Week X.
  `python -m database.populate_stats` does the same by hand.
  `python -m services.weight_learning --season 2026 --dry-run` shows what it would change.
- **Per position:** each position (QB/RB/WR/TE) also takes its own step from its own players
  that week, with the same caps, starting from the global weights; a position with fewer than
  30 scored players that week just follows the global step. So props can end up counting for
  more at QB and the experts more at WR if that's what the results show.
- **Storage:** each update is a row in the local `learned_weights` table. Delete the rows to
  go back to the starting weights.

On Week 7 of 2026 (before props were configured), the blend agreed closely with Hayden's
own ranks (rank correlation 0.93 for the top 24 RBs, 0.97 for the top 36 WRs) while still
moving players on matchup.

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
| POST | `/api/trade/evaluate` | Body `{"give": [ids], "get": [ids], "settings": {...}}`; values, verdict, balance suggestions |
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
| GET | `/api/my-team/{league_id}/trades?want_position=` | Trade offers with each real team, by their needs |
| GET | `/api/my-team/{league_id}/matchup` | This week's opponent, both lineups, win probability, expected/upside/safe lineups, the updated lineup and the bench |
| GET | `/api/my-team/{league_id}/playoffs` | Each player's playoff weeks (opponent, bye, projection), the best lineup per week, and planner fixes |
| GET | `/api/my-team/{league_id}/league` | Power rankings, simulated playoff odds and manager tendencies (live from Yahoo, cached 1 h) |
| GET | `/api/my-team/{league_id}/news?limit=` | Newest news across your roster |
| GET | `/api/players/{id}/news` | One player's latest news (Sleeper) |
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

Full TypeScript types for every response: [`docs/API.md`](docs/API.md).

## AI assistant and agent tools

With a Yahoo league imported, more tools answer from your real league:
- `get_my_team`: your roster, projections and lineup advice;
- `get_my_matchup`: this week's opponent, both lineups, win probability and the changes to make;
- `get_league_rosters`: any team's roster, or every team's top players and weak positions;
- `get_playoff_outlook`: your players across your league's playoff weeks, with planner fixes;
- `get_power_rankings`: every team's strength, record, playoff odds and manager tendencies;
- `get_player_news`: a player's latest headlines (any player, league or not);
- `get_league_waivers`: players free in your league;
- `find_league_trades`: offers with real teams (optionally only for players you name).

The assistant's instructions include each imported league and **your current roster**, so it
never asks which players you have. `find_trades` with no players uses your roster.

**Intent routing** (`services/agent_routing.py`): each question gets only the tools it's likely
to need (keywords from your last two messages, plus player tools when you name a player),
usually 6–12 of the 23. That saves tokens on Groq's free tier and cuts wrong tool picks.

**Chat memory:** when you state your record, goals or players you won't trade, the assistant
calls `remember`. Those facts show under "Keeping in mind" and go back with every message,
so they survive even though only the last 20 messages are sent. New chat clears them.

**Evaluation set** (`services/agent_eval.py`): 26 saved questions with the tools each should
use. The test suite checks that routing sends those tools; with `GROQ_API_KEY`,
`python -m services.agent_eval` runs them through the real model and checks tool choice,
grounding and that it never asks for your roster.

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

**Grounding:** before a chat answer is shown, every player name in it is checked against
the player database. If it names players that no tool returned and you didn't mention
(stale knowledge such as last year's rosters), the model gets one round to remove them or
look them up. Tool fields say what they are (`this_week_proj`, playoff schedule as "6%
harder than average") so the model can't mislabel a projection as an actual score.

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
(7 markets × 1 region). To fit the free plan, **each game's props are bought once, in the
72 hours before kickoff** (about 16 × 7 = 112 credits a week), so props show up two to three
days before each game. Every purchase is logged in the database, so restarts don't
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
| `API_TOKEN` | — | Optional. When set, every `/api` request except `/api/meta` needs `Authorization: Bearer <token>`; enter it once in Settings (stored in that browser) |
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
| `GROQ_API_KEY` | — | Enables the AI chat |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Groq model for the chat (free tier). `openai/gpt-oss-20b` is faster but invents players far more often |
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
  stored by type only, since messages can contain URLs with API keys.
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
  api/                      thin routers: data, market, integrations, agent, legacy
  services/                 tested business logic (scoring, projections, valuation, schedule strength,
                            weekly model and rankings, props math, leaderboards, players, trade, insights, agent)
  integrations/             sleeper, fantasypros, yahoo, yahoo experts, espn, the odds api, token encryption
  tools/                    agent tool registry and definitions
  mcp_server.py             MCP server over the same tools
  database/                 models, migrations helpers, data loader
  alembic/                  schema migrations
  tests/                    pytest suite
frontend/                   React + TypeScript + Vite web app
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
