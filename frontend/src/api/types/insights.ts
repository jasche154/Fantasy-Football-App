/** Kickers, defenses, matchup, accuracy, playoffs, league, news, compare, streaming. */
import type { PlayerSummary, Position } from './core';

/* ---------- Kickers, defenses, matchup, accuracy ---------- */

export interface KdstRow {
  rank: number;
  player: { id: number | null; name: string; position: string; team: string };
  opponent: string;
  projection: number;
  reasons: string[];
  fg_attempts?: number;
  fg_made?: number;
  pat_made?: number;
  wind?: number | null;
  temp?: number | null;
  indoors?: boolean;
  sacks?: number;
  takeaways?: number;
  opp_implied?: number;
}

export interface KdstResponse {
  season: number;
  week: number;
  position: 'K' | 'DST';
  results: KdstRow[];
}

export interface MatchupStarter {
  slot: string;
  player_id: number;
  name: string;
  position: string | null;
  team: string | null;
  opponent: string | null;
  projection: number;
  floor: number | null;
  ceiling: number | null;
  actual: number | null;
  status: string | null;
}

export interface MatchupSide {
  team: string;
  starters: MatchupStarter[];
  projected: number;
  yahoo_points: number | null;
  yahoo_projected: number | null;
}

export interface MatchupResponse {
  league: { id: number; name: string };
  week: number;
  me: MatchupSide;
  opponent: MatchupSide;
  win_probability: number;
  yahoo_win_probability: number | null;
  strategies: Record<string, { projected: number; win_probability: number }>;
  recommended: string;
  changes: { start: string[]; sit: string[] };
  simulations: number;
  /** The lineup after the recommended changes, and its projection / win chance. */
  updated?: { mode: string; starters: MatchupStarter[]; projected: number; win_probability: number };
  /** Everyone not in the updated lineup, best projection first (slot = where they sit in Yahoo now). */
  bench?: MatchupStarter[];
}

export interface HeldOutResult {
  before: { rmse: number; mae: number; rank_corr: number };
  after: { rmse: number; mae: number; rank_corr: number };
}

export interface ModelAccuracyResponse {
  season: number | null;
  live: {
    n: number;
    weeks: number[];
    blend_mae: number | null;
    bias_by_position?: Record<string, number>;
    range?: { n: number; below_floor: number | null; above_ceiling: number | null };
  };
  backtest: { seasons: number[]; held_out: Record<string, HeldOutResult> } | null;
  weight_history: { season: number; week: number; rows: number; weights: Record<string, number> }[];
}

export interface PlayoffCell {
  week: number;
  opponent: string | null;
  bye: boolean;
  factor: number | null;
  projection: number;
}

export interface PlayoffsResponse {
  league: string;
  team: string | null;
  weeks: number[];
  source: 'league' | 'default';
  players: { player: PlayerSummary; slot: string; ros_ppg: number; weeks: PlayoffCell[]; playoff_total: number }[];
  lineups: Record<string, { projected: number; starters: number[]; empty_slots: string[] }>;
  weakest_week: number | null;
  fixes?: PlayoffFix[];
}

export interface PlayoffFixOption {
  player: PlayerSummary;
  playoff_total: number;
  gain: number;
  owner: string | null;
}

export interface PlayoffFix {
  position: Position;
  weakest_starter: { name: string; playoff_total: number } | null;
  waivers: PlayoffFixOption[];
  trade_targets: PlayoffFixOption[];
}

export interface ManagerTendencies {
  moves: number;
  trades: number;
  label: 'Active trader' | 'Waiver regular' | 'Quiet' | null;
  trades_for: string[];
  trades_away: string[];
  picks_up: string[];
}

export interface PowerRankingRow {
  power_rank: number;
  team: string;
  team_key: string;
  is_users_team: boolean;
  record: string;
  standing: number | null;
  points_for: number;
  points_against: number;
  strength: number | null;
  remaining_opponent_strength: number | null;
  /** Best-ball lineup projection for each remaining week (byes covered by a waiver pickup). */
  weekly_projection?: Record<string, number>;
  playoff_odds?: number;
  top_seed_odds?: number;
  expected_wins?: number;
  tendencies: ManagerTendencies | null;
}

export interface LeagueOverview {
  league: string;
  playoff_teams: number;
  regular_season_weeks_left: number;
  simulations: number;
  teams: PowerRankingRow[];
}

export interface NewsItem {
  title: string;
  summary: string | null;
  analysis: string | null;
  source: string;
  url: string | null;
  published: string | null;
  player?: { id: number; name: string };
}

export interface NewsResponse {
  items: NewsItem[];
  note?: string;
  /** Roster news only: each player's newest item time, by player id. */
  latest?: Record<string, string>;
}

export interface CompareGame {
  season: number;
  week: number;
  opponent: string | null;
  fantasy_points: number;
  expected_points: number | null;
  targets: number;
  receptions: number;
  carries: number;
  scrimmage_yards: number;
  passing_yards: number;
  touchdowns: number;
  snap_pct: number | null;
}

export interface CompareSummary {
  season: number;
  games: number;
  ppg: number;
  expected_ppg: number | null;
  best: number;
  worst: number;
  targets_per_game: number;
  carries_per_game: number;
}

export interface CompareResponse {
  seasons: number[];
  players: { player: PlayerSummary; games: CompareGame[]; summary: CompareSummary[] }[];
}

export interface StreamingCell {
  opponent?: string | null;
  projection: number;
  early?: boolean;
  bye?: boolean;
}

export interface StreamingGrid {
  rows: { player: PlayerSummary; name: string; mine: boolean; by_week: Record<string, StreamingCell>; total: number;
    rush_ppg?: number }[];
  path: string[];
}

export interface StreamingResponse {
  league: string;
  weeks: number[];
  grids: Record<string, StreamingGrid>;
  note: string;
}
