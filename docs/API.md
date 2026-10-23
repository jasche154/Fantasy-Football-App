# Backend API contract

Base URL: `http://localhost:8000` (frontend reads `VITE_API_BASE`, default to that).
All responses are JSON. Errors use FastAPI's shape `{"detail": "message"}` with a 4xx/5xx
status (422 for validation errors, where `detail` is a list).

The API is local-only: requests with a Host other than `localhost` / `127.0.0.1` get 400,
and POSTs whose browser `Origin` isn't an allowed CORS origin get 403. Clients that send no
`Origin` (curl, scripts, MCP) are unaffected.

Positions supported everywhere: `QB | RB | WR | TE`.

## Shared query params — league settings
Used by every endpoint that values or scores players (`/api/players/{id}`, `/api/rankings`,
`/api/insights/*`). `POST /api/trade/evaluate` takes the same fields in `settings`.

| param | type | default | notes |
|---|---|---|---|
| `scoring` | `"ppr" \| "half" \| "std"` | `ppr` | |
| `teams` | int 4-20 | 12 | |
| `superflex` | bool | false | adds one QB/RB/WR/TE slot |
| `te_premium` | float 0-1 | 0 | extra points per TE reception |

## Types (TypeScript)

```ts
type Position = "QB" | "RB" | "WR" | "TE";
type Scoring = "ppr" | "half" | "std";

interface PlayerSummary { id: number; name: string; position: Position | string | null; team: string | null; }

interface Meta {
  current_season: number;
  latest_week: number | null;          // latest week with stats in DB for current_season
  seasons: number[];                   // seasons available in DB, ascending
  positions: Position[];
  scoring_presets: Scoring[];
  integrations: { fantasypros: boolean; yahoo_configured: boolean; yahoo_connected: boolean; llm: boolean; };
}

interface RankedPlayer {
  rank: number;                // overall rank by trade_value within the response
  pos_rank: string;            // e.g. "RB3"
  player: PlayerSummary;
  games: number;
  ppg: number;                 // actual fantasy points / game (this season, chosen scoring)
  expected_ppg: number | null; // nflverse ff_opportunity expected points / game
  ros_ppg: number;             // model projection, points / game
  vorp_per_game: number;
  trade_value: number;         // ROS points above replacement (>= 0)
  ecr: number | null;          // FantasyPros weekly ECR if available
}

interface RankingsResponse { season: number; through_week: number | null; scoring: Scoring; weeks_remaining: number; results: RankedPlayer[]; }

interface GameLogRow {
  week: number; season_type: string | null; opponent: string | null;
  fantasy_points: number; expected_points: number | null;
  passing_yards: number; passing_tds: number; interceptions: number;
  carries: number; rushing_yards: number; rushing_tds: number;
  targets: number; receptions: number; receiving_yards: number; receiving_tds: number;
  snap_pct: number | null;     // 0-100
}

interface PlayerProfile {
  player: PlayerSummary & { gsis_id: string | null };
  season: number;
  scoring: Scoring;
  summary: {
    games: number; fantasy_points: number; ppg: number;
    expected_ppg: number | null; fpoe_per_game: number | null;
    targets: number; receptions: number; carries: number;
    passing_yards: number; rushing_yards: number; receiving_yards: number; total_tds: number;
    target_share: number | null;   // 0-1, mean of weekly shares
    snap_pct: number | null;       // 0-100, mean
  };
  projection: { ros_ppg: number; vorp_per_game: number; trade_value: number; pos_rank: string | null; } | null;
  advanced: {
    rushing: Record<string, number | null> | null;
    receiving: Record<string, number | null> | null;
    passing: Record<string, number | null> | null;
  };
  game_log: GameLogRow[];             // ascending by week
  signals: { buy_low: boolean; sell_high: boolean; reasons: string[] };
}

// format "percent": value is already on a 0-100 scale (render 62.5 as "62.5%"), never a 0-1 fraction.
interface MetricDef { key: string; label: string; description: string; volume_label: string; format: "decimal" | "percent" | "integer"; higher_is_better: boolean; }
interface MetricCategory { key: string; label: string; metrics: MetricDef[]; }
interface LeaderboardRow { rank: number; player: PlayerSummary; value: number; volume: number; }
interface LeaderboardResponse { category: string; metric: string; label: string; season: number; min_volume: number; results: LeaderboardRow[]; }

interface TradeSidePlayer { player: PlayerSummary; trade_value: number; ros_ppg: number; vorp_per_game: number; weight: number; adjusted_value: number; }
interface TradeSide { players: TradeSidePlayer[]; raw_total: number; adjusted_total: number; }
interface BalanceSuggestion { player: PlayerSummary; trade_value: number; add_to: "give" | "get"; resulting_percent_difference: number; }
interface TradeResult {
  give: TradeSide;                    // what the user sends away
  get: TradeSide;                     // what the user receives
  difference: number;                 // get.adjusted_total - give.adjusted_total
  percent_difference: number;         // difference / max(sides), -1..1
  verdict: "fair" | "slight_win" | "slight_loss" | "big_win" | "big_loss";
  summary: string;                    // one plain-English sentence
  balance_suggestions: BalanceSuggestion[];
  weeks_remaining: number;
}

interface Insight {
  player: PlayerSummary;
  score: number;                      // higher = stronger signal
  games: number;
  ppg: number; expected_ppg: number; fpoe_per_game: number;
  td_diff: number | null;             // actual TDs - expected TDs
  recent_ppg: number;                 // last 3 games
  usage_trend: number | null;         // recent expected ppg - season expected ppg
  trade_value: number;
  reasons: string[];
}
interface InsightsResponse { season: number; through_week: number | null; scoring: Scoring; results: Insight[]; }

interface TrendingRow { player: PlayerSummary | null; sleeper_id: string; count: number; }

interface ExpertRankingRow { player: PlayerSummary | null; name: string; position: string; team: string | null; rank: number; ecr: number; pos_rank: string | null; best: number | null; worst: number | null; sd: number | null; }
// scope "positional": ranks restart at 1 for each position (the free weekly snapshot), so an
// unfiltered list is grouped by position. "overall": one ranking across positions (FantasyPros API).
interface ExpertRankingsResponse { source: "fantasypros_api" | "nflverse_ffrankings" | null; ranking_type: string | null; scope: "positional" | "overall" | null; season: number | null; week: number | null; results: ExpertRankingRow[]; warning: string | null; }

interface ToolDef { name: string; description: string; parameters: object; }
interface ChatMessage { role: "user" | "assistant"; content: string; }
interface ToolCallTrace { name: string; arguments: Record<string, unknown>; ok: boolean; }
interface ChatResponse { reply: string; tool_calls: ToolCallTrace[]; }
```

## Endpoints

| method | path | params / body | response |
|---|---|---|---|
| GET | `/api/health` | | `{status: "ok"}` |
| GET | `/api/meta` | | `Meta` |
| GET | `/api/players/search` | `q` (≥1 char), `position?`, `limit=10` | `{results: PlayerSummary[]}` (QB/RB/WR/TE only, best match first) |
| GET | `/api/players/{id}` | `season?` (default current), league params | `PlayerProfile` (404 if unknown) |
| GET | `/api/rankings` | `position?`, `limit=100`, league params | `RankingsResponse` |
| GET | `/api/leaderboards/metrics` | | `{categories: MetricCategory[]}` |
| GET | `/api/leaderboards` | `category`, `metric`, `season?`, `min_volume?` (default per metric), `limit=25`, `ascending=false`, `position?` | `LeaderboardResponse` |
| GET | `/api/rankings/rushing` | legacy (unchanged params) | `{metric, season, results: [{player, <metric>}]}` |
| POST | `/api/trade/evaluate` | `{give: number[], get: number[], settings?: {scoring, teams, superflex, te_premium}}` | `TradeResult` (422 if a side is empty, ids are unknown, or a player is repeated) |
| GET | `/api/insights/buy-low` | `position?`, `limit=10`, league params | `InsightsResponse` |
| GET | `/api/insights/sell-high` | same | `InsightsResponse` |
| GET | `/api/insights/trending` | `type=add\|drop`, `limit=10` | `{results: TrendingRow[]}` (502 if Sleeper unreachable) |
| GET | `/api/experts/rankings` | `position?`, `limit=50` | `ExpertRankingsResponse` |
| GET | `/api/agent/tools` | | `{tools: ToolDef[]}` |
| POST | `/api/agent/tools/{name}` | `{arguments: {...}}` | `{result: any}` (404 unknown tool or player, 422 bad args or season, 502 upstream source down, 503 no data) |
| POST | `/api/agent/chat` | `{messages: ChatMessage[]}` | `ChatResponse` (503 if no LLM key) |
| GET | `/api/integrations/status` | | `{fantasypros: bool, yahoo_configured: bool, yahoo_connected: bool, llm: bool}` |
| GET | `/api/integrations/yahoo/auth-url` | | `{url: string}` |
| POST | `/api/integrations/yahoo/exchange` | `{code: string}` | `{connected: true}` |
| GET | `/api/integrations/yahoo/leagues` | | `{leagues: [{league_key, name, season, num_teams, scoring_type}]}` |
| POST | `/api/integrations/yahoo/leagues/{league_key}/import` | | `{league_id, teams: number, roster_spots: number, unmatched_players: string[]}` |
