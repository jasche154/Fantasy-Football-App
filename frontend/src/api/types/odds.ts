/** The Odds page (GET /api/odds): stored lines and player props, never fetched live. */
import type { Position } from './core';

export type PropStatKey = 'pass_yds' | 'pass_tds' | 'ints' | 'rush_yds' | 'rec' | 'rec_yds' | 'scrimmage_tds';

export interface PlayerProp {
  player_id: number | null;
  name: string;
  position: Position | null;
  team: string | null;
  opponent: string | null;
  /** Prop-implied expected stats. */
  stats: Partial<Record<PropStatKey, number>>;
  fetched_at: string | null;
}

export interface OddsGame {
  home_team: string;
  away_team: string;
  gameday: string | null;
  gametime: string | null;
  /** UTC ISO time. */
  kickoff: string | null;
  final: boolean;
  home_score: number | null;
  away_score: number | null;
  /** Home team's expected margin (positive = home favored). */
  spread_line: number | null;
  total_line: number | null;
  home_implied: number | null;
  away_implied: number | null;
  props: PlayerProp[];
  props_fetched_at: string | null;
  props_hours_before_kickoff: number | null;
}

export interface OddsResponse {
  season: number;
  week: number;
  games: OddsGame[];
  props_count: number;
  note: string | null;
}
