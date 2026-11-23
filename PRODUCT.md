# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Today: one fantasy football player (the owner) running Gridiron Edge on their own computer to make real
decisions in their Yahoo leagues each week (lineups, waivers and FAAB bids, trades) and to play the Replay
League (a past NFL season against CPU managers) for fun and practice. Later: friends and other fantasy
players, so features should not assume a single private user where that is cheap to avoid (a public
rankings site is already in progress).

## Product Purpose

"Your fantasy platform tells you what happened. This tells you what to do next." Gridiron Edge turns free,
open NFL data plus outside sources into decisions: who to start, who to add and for how much, which trades
to make, and how likely you are to win. Success is a faster, better call every week, with the reasoning one
click away.

## Positioning

Every number comes from real data and deterministic, tested code, blended from several sources (Vegas
player props, Yahoo experts and consensus, ESPN, and the app's own opponent-adjusted model) with each
source shown side by side, and judged out of sample. The AI assistant only explains numbers that came back
from a tool call; it never invents them.

## Operating Context

- Weekly fantasy rhythm: waivers midweek, lineup decisions before kickoff (with late-game FLEX
  locks), trades during the season, playoffs in the final weeks.
- Connected to the user's Yahoo leagues; vocabulary and screens mirror Yahoo Fantasy (My Team, Matchup,
  Players, Trades, League, waivers, FAAB, IR).
- Replay League: a full season replayed week by week, full screen, with a draft, waivers, trades, playoffs
  and a consolation bracket.
- Runs on localhost (FastAPI + React), desktop first but must work at phone width.

## Capabilities and Constraints

- Weekly rankings with each source's projection, injury-aware adjustments, floor/ceiling ranges,
  start/sit, waiver plans with named drops, trade evaluation on both real rosters, matchup win
  probability, playoff odds, strength of schedule, advanced-stat leaderboards, Chart Studio, Ask AI.
- Every displayed number is labeled by kind: Actual, Expected, Projection / trade value, ECR, Week X
  projection.
- Replay League no-hindsight rule: views and CPU logic only ever use what was knowable before that week.
- Localhost security model; secrets stay in backend/.env and are never shown.
- Open: when and how other users get access (hosting, accounts) is undecided.

## Brand Commitments

- Name: Gridiron Edge.
- Familiar to Yahoo Fantasy players in layout and terms.
- Every screen works in light and dark mode and at phone width.

## Evidence on Hand

- README.md documents every feature and how each number is computed.
- Backtests and evaluation CLIs (services.backtest, projection_eval, agent_eval) produce honest accuracy
  numbers; quote only numbers they actually produced.
- No users, testimonials or press exist; never fabricate them.

## Product Principles

1. Decision first: lead with the answer (start him, add him, accept or decline), with the numbers behind
   it one click away.
2. Every number is real and labeled; uncertainty is shown, not hidden.
3. Nothing from the future: point-in-time data only, in Replay and in every evaluation.
4. Feels like the fantasy app the user already knows, only smarter.
