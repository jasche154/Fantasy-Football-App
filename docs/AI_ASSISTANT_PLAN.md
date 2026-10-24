# Ask AI: plan for handling sophisticated requests

*Written 2026-10-24. Covers the error you hit, what users actually ask, how to build for it, and the order to build it in.*

## 1. The error from the example conversation

> **User:** "I'm 5-1 and want to stack my team for a good playoff schedule… players I can trade: Bryce Young, Jonathan Taylor, Denzel Boston, Jameson Williams, Chris Olave, Ollie Gordon, Braelon Allen, Germie Bernard, Davante Adams"
> **App:** "The language model request failed: BadRequestError"

### Root cause, reproduced

Groq returned **HTTP 400, code `tool_use_failed`**:

> parameters for tool find_trades did not match schema: `/my_players: maxItems: got 9, want 6`

- **What happened:** the model correctly called `find_trades` with all 9 players. Our tool schema capped `my_players` at 6.
- **Why the whole chat failed:** Groq validates tool arguments *on its side* before our code ever sees them. A schema violation therefore fails the entire request, not just the tool, and the app showed a generic error.

### Second issue found while fixing it

With the call accepted, the next request hit Groq's **free-tier limit: 8,000 tokens per minute** (`413 rate_limit_exceeded`, 8,478 requested). Two things filled the budget:
- the 17 tool definitions, at roughly 3k tokens per request;
- full trade-evaluation payloads.

### Fixes (shipped with this doc)

| Fix | Where |
|---|---|
| `find_trades` accepts up to 10 players, matching the REST API | `tools/definitions.py` |
| A rejected tool call is fed back to the model as a system note ("your call was rejected: …, fix the arguments"), and the round is retried instead of failing the chat | `services/agent.py` (`ToolCallRejected`) |
| Compact tool results: trade offers and waiver rows become one line each, with the target's **playoff schedule factor (weeks 15–17)** added | `tools/definitions.py` |
| Unknown names (e.g. a rookie with no stats yet) are skipped and reported in `skipped_players`, instead of failing the whole call | `_find_trades` |
| Tool results capped at 6,000 characters (was 12,000) | `MAX_TOOL_RESULT_CHARS` |
| Rate limits show a plain message ("free-tier limit reached; wait a minute or ask a narrower question") | `_rate_limited` |

The example conversation now completes. The model calls `find_trades` once for QB and once for WR, then returns a table of offers with ROS points per game and playoff schedule factors.

## 2. What users ask (and which tools answer it)

Common requests, from fantasy communities, start/sit and trade threads, and the features competitors advertise:

| # | Use case | Example | Tools today | Gap |
|---|---|---|---|---|
| 1 | Start/sit | "Allen or Hall this week?" | `get_weekly_rankings`, `explain_projection` | A `compare_week` tool (start/sit in one call) |
| 2 | Why is X ranked here? | "Why is Puka WR1?" | `explain_projection` | — |
| 3 | Trade evaluation | "Is Olave for Henry fair?" | `evaluate_trade` | — |
| 4 | Trade *search* with goals | "Upgrade QB and RB1 for the playoffs" (this example) | `find_trades` | Multi-position search in one call; optimizing for playoff schedule, not just value |
| 5 | Playoff / schedule stacking | "Who has the easiest weeks 15–17?" | `get_schedule_strength` | A playoff-only ranking tool |
| 6 | Waivers / FAAB | "Who should I pick up? How much to bid?" | `get_waiver_targets` | League-specific availability (needs league import) |
| 7 | Injury fallout | "Hall is questionable; who benefits?" | `get_weekly_rankings` (scenarios), flags | A dedicated `injury_impact` tool |
| 8 | Buy low / sell high | "Who should I sell before they cool off?" | `get_buy_low_candidates`, `get_sell_high_candidates` | — |
| 9 | Breakouts | "Who's about to break out?" | flags (via weekly rankings) | A `get_flags` tool |
| 10 | Roster-aware advice | "Given my team, what should I do?" | none (no roster context) | **My Team context**: a saved roster passed to every tool |
| 11 | Stat lookups | "Who leads in RYOE?" | `get_leaderboard` | — |
| 12 | Dynasty / keeper | "Trade Allen for a 2027 1st?" | none | Out of scope for now |

## 3. Best practices for building them

1. **Design tool schemas with generous, explicit limits.** Providers validate tool arguments before we see them, so a too-tight `maxItems` breaks the whole conversation. Match the REST limits, and say the limit in the description ("up to 10 players; call again for more").
2. **Recover; never crash the chat.** Treat provider-side validation errors as feedback the model can act on (done). The same goes for unknown players: skip them and report.
3. **Budget tokens like money.** Free Groq allows 8k tokens a minute.
   - Return compact rows (names, a few numbers, one reason), not full objects.
   - Send only the tools relevant to the request: route by intent (trade, start/sit, waivers, schedule) so a request carries 4–6 tools instead of 17.
   - Summarize earlier tool results instead of resending them.
4. **One goal, one tool call.** Complex asks ("upgrade QB and RB1, or RB1 → WR2") should split into separate calls per position or goal. Say this in the system prompt and the tool descriptions.
5. **Clarify only what's missing.** Asking for the tradeable players, as in the example, was right. Ask once, list the options, and remember the answer for the rest of the chat.
6. **Ground every number.** The system prompt already forbids inventing stats. Add "cite the tool and the field". Example: the model labeled Denzel Boston as a QB-upgrade chip without checking his value, and that should be caught.
7. **Expose the user's perspective explicitly.** Keep fields like `perspective` and `your_gain_pct` so the model doesn't confuse whose side a verdict favors.
8. **Evaluate with a fixed prompt set.** Keep 20–30 real questions, one per use case above, with expected tool calls and checks. Run them after every prompt or tool change (`python -m testing.groq_test`).

## 4. Roadmap

| Priority | Item | Why |
|---|---|---|
| P0 | Intent routing: send a relevant subset of tools per message | Fits the free token budget; fewer wrong tool picks |
| P0 | `playoff_targets` tool: players with the best weeks 15–17 schedule × ROS value, by position | Directly answers the example's goal |
| P0 | Multi-position `find_trades` (`want_positions: ["QB","RB","WR"]`) plus an "optimize for playoffs" flag | One call instead of several |
| P1 | My Team context: a saved roster (manual entry or Sleeper import) included automatically | Unlocks roster-aware answers (#10) |
| P1 | `compare_week` and `injury_impact` tools | Faster, cheaper start/sit and injury answers |
| P1 | Conversation memory: carry the user's roster, record and tradeable players across turns | No re-asking |
| P2 | Model choice: an option for a larger model or paid tier for long multi-step plans | Better reasoning when the budget allows |
| P2 | An evaluation harness of scripted conversations, scored for tool choice and grounded numbers | Prevents regressions like this one |
