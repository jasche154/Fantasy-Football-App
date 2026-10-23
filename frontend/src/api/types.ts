/**
 * Backend API types. Mirrors docs/API.md exactly; that file is the source of truth.
 */

export type Position = 'QB' | 'RB' | 'WR' | 'TE';
export type Scoring = 'ppr' | 'half' | 'std';

export interface PlayerSummary {
  id: number;
  name: string;
  position: Position | string | null;
  team: string | null;
}

export interface IntegrationsStatus {
  fantasypros: boolean;
  yahoo_configured: boolean;
  yahoo_connected: boolean;
  llm: boolean;
}

export interface Meta {
  current_season: number;
  latest_week: number | null;
  seasons: number[];
  positions: Position[];
  scoring_presets: Scoring[];
  integrations: IntegrationsStatus;
}

/** League settings shared by every valuation endpoint (query params or trade body). */
export interface LeagueSettings {
  scoring: Scoring;
  teams: number;
  superflex: boolean;
  te_premium: number;
}

export interface RankedPlayer {
  rank: number;
  pos_rank: string;
  player: PlayerSummary;
  games: number;
  ppg: number;
  expected_ppg: number | null;
  ros_ppg: number;
  vorp_per_game: number;
  trade_value: number;
  ecr: number | null;
}

export interface RankingsResponse {
  season: number;
  through_week: number | null;
  scoring: Scoring;
  weeks_remaining: number;
  results: RankedPlayer[];
}

export interface GameLogRow {
  week: number;
  season_type: string | null;
  opponent: string | null;
  fantasy_points: number;
  expected_points: number | null;
  passing_yards: number;
  passing_tds: number;
  interceptions: number;
  carries: number;
  rushing_yards: number;
  rushing_tds: number;
  targets: number;
  receptions: number;
  receiving_yards: number;
  receiving_tds: number;
  /** 0-100 */
  snap_pct: number | null;
}

export interface PlayerSeasonSummary {
  games: number;
  fantasy_points: number;
  ppg: number;
  expected_ppg: number | null;
  fpoe_per_game: number | null;
  targets: number;
  receptions: number;
  carries: number;
  passing_yards: number;
  rushing_yards: number;
  receiving_yards: number;
  total_tds: number;
  /** 0-1 fraction, mean of weekly shares */
  target_share: number | null;
  /** 0-100, mean */
  snap_pct: number | null;
}

export interface PlayerProjection {
  ros_ppg: number;
  vorp_per_game: number;
  trade_value: number;
  pos_rank: string | null;
}

export type AdvancedStatGroup = Record<string, number | null>;

export interface PlayerProfile {
  player: PlayerSummary & { gsis_id: string | null };
  season: number;
  scoring: Scoring;
  summary: PlayerSeasonSummary;
  projection: PlayerProjection | null;
  advanced: {
    rushing: AdvancedStatGroup | null;
    receiving: AdvancedStatGroup | null;
    passing: AdvancedStatGroup | null;
  };
  game_log: GameLogRow[];
  signals: { buy_low: boolean; sell_high: boolean; reasons: string[] };
}

export type MetricFormat = 'decimal' | 'percent' | 'integer';

export interface MetricDef {
  key: string;
  label: string;
  description: string;
  volume_label: string;
  /** "percent" values are already on a 0-100 scale. */
  format: MetricFormat;
  higher_is_better: boolean;
}

export interface MetricCategory {
  key: string;
  label: string;
  metrics: MetricDef[];
}

export interface LeaderboardRow {
  rank: number;
  player: PlayerSummary;
  value: number;
  volume: number;
}

export interface LeaderboardResponse {
  category: string;
  metric: string;
  label: string;
  season: number;
  min_volume: number;
  results: LeaderboardRow[];
}

/** Legacy `/api/rankings/rushing` response: each row carries the metric under its own key. */
export interface RushingLeadersResponse {
  metric: string;
  season: number;
  results: Array<{ player: PlayerSummary | string } & Record<string, unknown>>;
}

export interface TradeSidePlayer {
  player: PlayerSummary;
  trade_value: number;
  ros_ppg: number;
  vorp_per_game: number;
  weight: number;
  adjusted_value: number;
}

export interface TradeSide {
  players: TradeSidePlayer[];
  raw_total: number;
  adjusted_total: number;
}

export type TradeSideKey = 'give' | 'get';

export interface BalanceSuggestion {
  player: PlayerSummary;
  trade_value: number;
  add_to: TradeSideKey;
  resulting_percent_difference: number;
}

export type TradeVerdict = 'fair' | 'slight_win' | 'slight_loss' | 'big_win' | 'big_loss';

export interface TradeResult {
  give: TradeSide;
  get: TradeSide;
  difference: number;
  /** difference / max(sides), -1..1. Positive favours the user. */
  percent_difference: number;
  verdict: TradeVerdict;
  summary: string;
  balance_suggestions: BalanceSuggestion[];
  weeks_remaining: number;
}

export interface TradeRequest {
  give: number[];
  get: number[];
  settings?: LeagueSettings;
}

export interface Insight {
  player: PlayerSummary;
  score: number;
  games: number;
  ppg: number;
  expected_ppg: number;
  fpoe_per_game: number;
  td_diff: number | null;
  recent_ppg: number;
  usage_trend: number | null;
  trade_value: number;
  reasons: string[];
}

export interface InsightsResponse {
  season: number;
  through_week: number | null;
  scoring: Scoring;
  results: Insight[];
}

export interface TrendingRow {
  player: PlayerSummary | null;
  sleeper_id: string;
  count: number;
}

export interface ExpertRankingRow {
  player: PlayerSummary | null;
  name: string;
  position: string;
  team: string | null;
  rank: number;
  ecr: number;
  pos_rank: string | null;
  best: number | null;
  worst: number | null;
  sd: number | null;
}

export interface ExpertRankingsResponse {
  /** null when no rankings have been loaded yet. */
  source: 'fantasypros_api' | 'nflverse_ffrankings' | null;
  ranking_type: string | null;
  /** "positional": ranks restart at 1 for each position (free weekly snapshot). */
  scope: 'positional' | 'overall' | null;
  season: number | null;
  week: number | null;
  results: ExpertRankingRow[];
  /** Set when a FantasyPros refresh failed and stored rankings are shown instead. */
  warning: string | null;
}

export interface ToolDef {
  name: string;
  description: string;
  parameters: object;
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface ToolCallTrace {
  name: string;
  arguments: Record<string, unknown>;
  ok: boolean;
}

export interface ChatResponse {
  reply: string;
  tool_calls: ToolCallTrace[];
}

export interface YahooLeague {
  league_key: string;
  name: string;
  season: number;
  num_teams: number;
  scoring_type: string;
}

export interface YahooImportResult {
  league_id: number;
  teams: number;
  roster_spots: number;
  unmatched_players: string[];
}
