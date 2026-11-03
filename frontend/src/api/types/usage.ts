/** Player page: position percentiles and "what changed" (GET /api/players/{id}/usage). */
import type { Position } from './core';

export type PercentileDirection = 'higher' | 'lower' | 'context';

export interface PercentileStat {
  key: string;
  label: string;
  value: number | null;
  /** "%" when the value is already a percentage (0-100), "" otherwise. */
  unit: string;
  /** 0-100, already flipped for lower-is-better stats; null with a small sample. */
  percentile: number | null;
  direction: PercentileDirection;
  small_sample: boolean;
  sample: number;
  min_sample: number;
  sample_unit: string;
  pool_size: number;
}

export interface PlayerPercentiles {
  position: Position | null;
  pool_min_games: number;
  pool_players: number;
  stats: PercentileStat[];
}

export interface UsageChange {
  key: string;
  label: string;
  /** "%" = share (delta in percentage points), "/g" = per game. */
  unit: '%' | '/g';
  recent: number;
  before: number;
  delta: number;
  flag: 'up' | 'down' | null;
  flag_at: number;
}

export interface UsageChanges {
  recent_games: number;
  recent_weeks: number[];
  baseline_games: number;
  metrics: UsageChange[];
  note: string | null;
}

export interface PlayerUsageResponse {
  player_id: number;
  season: number | null;
  percentiles: PlayerPercentiles | null;
  changes: UsageChanges | null;
}
