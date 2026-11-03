/** Trade calculator request and result types. */
import type { LeagueSettings, PlayerSummary } from './core';

export interface TradeSidePlayer {
  player: PlayerSummary;
  trade_value: number;
  ros_ppg: number;
  vorp_per_game: number;
  pos_rank?: string;
  /** Share of remaining games he is expected to play (injury, or a backup role). */
  availability?: number;
  /** Null when his team isn't in the loaded schedule. */
  games_left?: number | null;
  schedule_factor?: number;
  playoff_factor?: number;
  expert_ros_ppg?: number | null;
  injury_note?: string | null;
  fill_in?: string | null;
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
  /** The same trade by expert-consensus (market) values, -1..1; positive favours the user. */
  market_percent_difference?: number | null;
  /** Only when the trade was scored on an imported league (`league_id`). */
  lineup?: TradeLineupImpact | null;
  /** n-for-m trades only: how far the package discount moves the gap (points > 0 favour you). */
  consolidation?: TradeConsolidation | null;
  /** Any verdict but fair: the value of one more player that evens the trade. */
  even_it?: TradeEvenIt | null;
}

export interface TradeConsolidation {
  /** "you" when you get fewer players than you send. */
  side: 'you' | 'them';
  points: number;
  note: string;
}

export interface TradeEvenIt {
  add_to: TradeSideKey;
  value: number;
  /** The balance suggestion that comes closest to even, if any. */
  closest: { player: PlayerSummary; trade_value: number } | null;
}

/** Points each real starting lineup gains over the rest of the season. */
export interface TradeLineupImpact {
  /** Null when the trade can't be scored on this league (see `note`). */
  your_gain: number | null;
  their_gain: number | null;
  their_team: string | null;
  /** Who they drop to fit the extra player, and the free agent for a spot you open. */
  their_drop?: PlayerSummary | null;
  your_add?: PlayerSummary | null;
  /** Who you drop when you take on more players than you send with a full roster. */
  your_drop?: PlayerSummary | null;
  note?: string | null;
  /** e.g. "Helps both lineups", "Helps you, costs them" (gains within 0.5 pts count as even). */
  lineup_label?: string | null;
}

export interface TradeRequest {
  give: number[];
  get: number[];
  settings?: LeagueSettings;
  /** Score on this imported league: its settings, both rosters and lineup gains. */
  league_id?: number;
}

