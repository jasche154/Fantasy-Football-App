# What we can take from GWTTKB Redraft (research 2026-11-02)

Five research passes over https://www.gwttkb.com/redraft/ and every redraft tool it links. The site is
static HTML + vanilla JS on Vercel, with public JSON in `/data/` and a few serverless endpoints
(`/api/board`, `/api/live`, `/api/chat`, `/api/yahoo`, `/api/espn-sync`). Almost all logic runs in the
browser, so most of it could be read directly; claims below come from their code comments and data files
unless marked as a guess.

## How their redraft numbers are made

| Piece | How it works | Compared with us |
|---|---|---|
| Rest-of-season rankings | **Hand-ranked** by the site for 12-team 1QB PPR/Half/Std. Other league sizes are derived with position/tier multipliers (they publish holdout MAE: 6.6 ranks, 2.1 for the top 50). Superflex moves QBs up and keeps the rest in order. Updated weekly after games. | We model rest-of-season values for any league settings. Theirs is editorial. |
| Value scale | A curve on **rank**, not points: `100*exp(-0.0885*(r-1)^0.64)`, scaled to 0–9999, with a replacement cliff at rank 156 (12 teams × 13 starters). This one value feeds the trade calculator, the AI Coach and the power rankings. | Our values come from VORP and points. Theirs is simpler and consistent across pages. |
| Season projections | Volume (team share × team volume, regressed) × efficiency (career rates, recency-decayed, shrunk to the position mean). KNN comparable players set the range. Then **anchored** to the editor's manual projection. Per-position replacement depth `{QB 1.17, RB 2.83, WR 3.67, TE 0.75}` per team, with RB/WR replacement shallower in half/standard scoring. Points for each extra point over replacement scaled by position and league size (`POS_SLOPE`, regressed from their boards, R² 0.94–0.99). | Similar ingredients. Their format-dependent replacement and league-size slope are worth checking against our VORP. |
| Weekly projections | **Sportsbook prop curves**: each book's main and alternate lines are devigged, pooled across 4 books at every threshold, forced monotone with isotonic regression, and the mean taken as `sum of P(X ≥ x)` (a true mean, not the median line). TD and rushing probabilities were calibrated against about 47k graded lines (Platt / power rules). Stats with no market come from a consensus sheet. K and DST are consensus only. | We blend props, Yahoo experts, ESPN and our model with learned weights, but we read **main lines only** and treat yardage as normal around the median. |
| Weekly ranges | A Gaussian copula over hard-coded stat correlations (rec↔recYds 0.85, …), 1,500 simulations per player, giving "chance of 15+/20+ points" and per-stat ladders ("75+ rec yds 28%"). | Our floor/ceiling are empirical q10/q90 (measured, a strength), but we show no threshold odds. |
| Trade calculator | Board value plus a KTC-style consolidation premium: `raw(p)=p*[0.010*(p/v)^8 + 0.300*min(p/t,1)^1.40 + 0.100]`, fitted to 41 KTC trades. Fair if the gap is under 6%. Balance suggestions only from the losing manager's real roster: within 5%, or closing at least 90% of the gap. Lineup impact is a one-week greedy points-per-game lineup with no forced drops. | Our league trade scoring (rest-of-season lineup gain, forced drops, acceptability floor) is stronger. Theirs **explains** consolidation better. |
| Trade targets / waivers / FAAB | **Hand-written weekly JSON.** FAAB is a % of the original budget for 8/10/12/14/16 teams, plus "min league size" and "highest waiver priority worth using". | Ours are computed and league-specific. Their presentation is clearer. |
| Power rankings | Board value through a stud premium, starters weighted 1.0, bench decaying 0.72·0.8^k, actual results blended in at `gp/(gp+9)` capped at 0.6, score `80+9z`. **Luck = actual wins − all-play expected wins.** 10,000-season seeded Monte Carlo. They admit the bench constants are "honest estimates, not measured". | We have seeded playoff odds and tendencies, but **no luck stat**. |
| AI Coach | **No tool calls.** The browser builds context blocks and the model (Claude Haiku 4.5) narrates. Many questions are answered **without the LLM** ("lab" answers for roster, lineup, trade history, live). A post-check flags "pick up X" when X is already rostered, and any number not in the prompt. Prompt rules: "confidence follows the source", "a downgrade states the PPG delta", and follow-up `[[CHIPS: …]]` options. Free: 3 deep answers a day. | We use tool calling with grounding. Their no-LLM answers and roster check fit our free-tier rate-limit problems. |
| Scout | Sleeper public API only. Manager tags with proof: Active trader (≥3 trades), Trade shark (wins by ≥12%), Overpays, FAAB whale (≥12% average bid), Sits on FAAB, Waiver hawk (≥1.5 adds per week), Chases hot names (≥3 lost bids), "X hunter" (≥50% of acquisitions at one position). | We have tendencies from Yahoo. We could add more tags and show proof. |
| Draft tools | ADP + noise bots, survival odds per pick, TAKE / SPRINT / DON'T SPEND / FADE bands, and draft grades as points above the league's expected-by-slot curve with z-score letters. | We have no draft tools (off-season only). |

## Plan (redraft, in-season first)

Ordered by value for effort. Each item follows our conventions: tests, out-of-sample checks for model
changes, README kept in sync.

### Phase 1: quick wins (S, about a day each)
1. **Luck and all-play record in power rankings** (`league_insights.py`, `PowerRankings` view). Luck = actual wins − all-play expected wins, plus "if you played everyone: 52–33". Backward-looking, no model risk.
2. **Ask AI roster check** (`services/grounding.py`): flag any "add/pick up X" when X is already on a roster in the league, and any "drop X" when X isn't on the user's roster. One revision, then a caveat, as grounding already works.
3. **Ask AI prompt rules** (`services/agent_prompt.py`): "a downgrade must state the PPG change", "confidence follows the source", and an explicit list of capabilities so the model never says it can't price a trade. Add eval cases.
4. **Consolidation shown in trade verdicts** (`services/trade.py`, `league_trades.py`, `TradeView`): name the 2-for-1 effect as its own line ("roster-spot / stud premium: −X") and give an "add about X to even it" figure. Our lineup-gain engine already prices this implicitly; this makes it visible.
5. **Waiver presentation** (`waiver_advice.py`, Waivers view): bid as a share of the *original* budget, and in rolling-priority leagues "worth your #N priority or better". Generic (no league) waivers get suggested bids by league size.
6. **Clearer trade-lineup messages**: a short label for each before/after case for both teams (both better, only you, only them) on top of the gains we already compute.
7. **A test that league size and superflex change value, never points** (their invariant). Cheap guard on `valuation.py`.

### Phase 2: model and data (M, a few days each, judged out of sample)
8. **Threshold odds on weekly projections**: P(15+/20+ points) and per-stat ladders from a copula simulation. Our version would estimate the stat correlations from nflverse weekly data (they hard-code theirs) and keep our empirical q10/q90 as the anchor. Shown in `explain_projection` and the weekly row.
9. **Calibrate prop probabilities against outcomes** (`props_math.py`): grade stored props against actual stats point-in-time (we keep `weekly_source_projections` and `projection_history`) and fit a TD / yardage correction like their Platt and power rules. Judge by RMSE on held-out weeks.
10. **Format-aware replacement level and a league-size slope**: test their idea that RB/WR replacement gets shallower in half/standard scoring and that only the *worth* of a point (not replacement points) moves with league size. Adopt only if leave-season-out trade/VORP accuracy improves.
11. **More manager tags, with proof** (`league_insights.py`): Trade shark / Overpays (past trades graded with `TradeScorer`), Sits on FAAB, Chases hot names (lost bids), position hunter. Each tag shows its evidence.
12. **Retro trade grades**: score each past league trade as rest-of-season points received − sent (with replacement credit for freed spots), next to what `TradeScorer` said at the time. Also an out-of-sample check of our trade model.
13. **Ask AI answers without the LLM** for common questions (my roster, my lineup, standings, a single trade value): routing sends them straight to a tool and a template. Saves Groq/Gemini quota, our main pain point.
14. **Player profile percentiles and "what changed"**: position percentiles with a minimum sample per stat (flipped when lower is better, "context" for aDOT), and a last-3-games vs. before usage delta (snap/target/air-yard share).

### Phase 3: bigger features (L, only if wanted)
15. **Full prop curves** (alternate lines, isotonic pooling, mean = sum of the survival curve). Most accurate weekly approach, but alternate-line markets multiply Odds API credits (500/month free), so it probably needs a paid plan or fewer games.
16. **Sleeper league import by username** (public API, no auth). Opens the app to Sleeper leagues and enables scouting a manager across all their leagues. ESPN by pasted cookies: skip, since storing session cookies cuts against our security model.
17. **Draft season tools** (ADP-noise mock bots, survival odds, need-aware best available, draft grades by league slot curve). The biggest gap against them, but off-season only and needs an ADP source.
18. **Live matchup win probability** (points so far + rest of game with a pace blend, our copula sim). Needs a live stats feed.

### Not worth copying
- Hand-made rankings and hand-written waiver/target lists (we compute ours; that's our edge).
- A rank-based value curve in place of points (simpler, but loses league-specific accuracy).
- Parlays, the pooled multi-league contest, ESPN cookie import.

## Open questions for you
- Which phase to start with? Phase 1 items are independent and can be split into parallel PRs.
- Is Sleeper support (item 16) wanted, or stay Yahoo-only?
- Is a paid Odds API plan on the table (item 15)?
