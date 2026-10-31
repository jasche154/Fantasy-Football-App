/** Week X rankings and schedule strength. */
import type { PlayerSummary, Position, Scoring, TradeResult } from './core';

/* ---------- Week X rankings & schedule strength ---------- */

export type WeeklySourceKey = 'vegas_props' | 'josh_hayden' | 'model' | 'yahoo_consensus' | 'espn' | 'experts';

export interface WeeklySourceWeight {
  key: WeeklySourceKey;
  label: string;
  weight: number;
  /** How many ranked players this source covers this week. */
  players: number;
}

export interface WeeklyMatchup {
  opponent: string;
  home: boolean;
  /** Opponent-adjusted defense factor at the player's position (>1 = easier). */
  sos_factor: number;
  /** 1 = easiest defense at the position. */
  sos_rank: number | null;
  implied_total: number | null;
  team_ppg: number | null;
  game_final: boolean;
}

export interface WeeklySourceValue {
  points: number;
  /** Positional rank behind expert-derived points. */
  rank: number | null;
}

export interface PlayerFlag {
  key: 'opportunity' | 'competition_returning' | 'role_growth' | 'td_luck' | 'efficiency' | 'injury' | string;
  kind: 'breakout' | 'regression' | 'risk';
  direction: 'up' | 'down';
  label: string;
  detail: string;
}

export interface InjuryScenario {
  teammate: string;
  teammate_status: string;
  extra_points: number;
  projection: number;
}

export interface WeeklyPlayer {
  rank: number;
  pos_rank: string;
  player: PlayerSummary;
  /** Expected points: if-active projection times the chance to play. */
  projection: number;
  if_active: number;
  play_prob: number;
  /** Injury designation and practice status, or null when healthy. */
  status: string | null;
  floor: number | null;
  ceiling: number | null;
  /** Points added because injured teammates may sit. */
  boost_points: number;
  pct_rostered: number | null;
  scenarios: InjuryScenario[];
  matchup: WeeklyMatchup;
  sources: Partial<Record<WeeklySourceKey, WeeklySourceValue>>;
  expert_ranks: Record<string, number>;
  /** Points scored that week in your scoring, once his game is final; null before (or if he sat). */
  actual?: number | null;
  flags: PlayerFlag[];
}

export interface ExplanationStep {
  label: string;
  value: string;
  detail: string;
}

export interface ProjectionExplanation extends WeeklyPlayer {
  season: number;
  week: number;
  steps: ExplanationStep[];
}

export interface FlagsResponse {
  season: number;
  week: number;
  results: WeeklyPlayer[];
}

export interface WaiverCandidate extends WeeklyPlayer {
  ros_value: number;
  week_value: number;
  schedule_factor: number;
  score: number;
  faab_pct: number;
  faab_label: string;
  reasons: string[];
}

export interface WaiversResponse {
  season: number;
  week: number;
  max_rostered: number;
  results: WaiverCandidate[];
}

export interface TradeOffer {
  give: PlayerSummary[];
  get: PlayerSummary;
  get_ros_ppg: number;
  market_percent_difference?: number;
  /** Rough chance the other manager accepts (0-1), from how the market sees the deal for them. */
  accept_chance?: number;
  /** League trades: who they drop to fit a 2-for-1, and the free agent for the spot you open. */
  their_drop?: PlayerSummary | null;
  your_add?: PlayerSummary | null;
  evaluation: TradeResult;
}

export type SourceRefreshState = 'updated' | 'fresh' | 'not_configured' | 'disabled' | 'busy' | 'refreshing' | 'error';

export interface SourceRefreshStatus {
  state: SourceRefreshState;
  rows: number | null;
  message: string | null;
}

export interface WeeklyRankingsResponse {
  season: number;
  week: number;
  scoring: Scoring;
  weights: WeeklySourceWeight[];
  /** The last completed week the weights were nudged by (null = the starting weights). */
  weights_learned_through?: number | null;
  sos_tilt: number;
  /** null when the backend skipped refreshing outside sources. */
  refresh: Record<string, SourceRefreshStatus> | null;
  results: WeeklyPlayer[];
}

export interface WeeklyRefreshResponse {
  season: number;
  week: number;
  sources: Record<string, SourceRefreshStatus>;
}

export interface DefenseStrengthRow {
  rank: number;
  team: string;
  sos_factor: number;
  games: number;
  allowed_per_game: number;
  this_week_opponent: string | null;
}

export interface OffenseScheduleRow {
  team: string;
  this_week: string | null;
  next_weeks: { opponent: string; sos_factor: number }[];
  rest_of_season: number;
  games_left: number;
}

export interface ScheduleStrengthResponse {
  season: number;
  week: number;
  position: Position;
  defenses: DefenseStrengthRow[];
  offenses: OffenseScheduleRow[];
}
