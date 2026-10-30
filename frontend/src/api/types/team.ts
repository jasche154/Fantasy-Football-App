/** My Team: imported Yahoo leagues, waivers, trades. */
import type { PlayerSummary, Position, Scoring } from './core';
import type { TradeOffer, WaiverCandidate, WeeklyPlayer } from './weekly';

/* ---------- My Team (imported Yahoo leagues) ---------- */

export interface MyLeague {
  id: number;
  key: string;
  name: string;
  season: number;
  team: string | null;
  scoring: Scoring;
  teams: number | null;
  starters: Record<string, number> | null;
  week: number | null;
}

export interface MyLeaguesResponse {
  leagues: MyLeague[];
}

export interface MyTeamRosterRow {
  /** Yahoo slot: QB, RB, WR, TE, W/R/T, BN, IR, ... */
  slot: string;
  starting: boolean;
  in_best_lineup: boolean;
  player: PlayerSummary;
  /** This week's projection row; null on a bye, ruled out, or not covered. */
  week: WeeklyPlayer | null;
  ros_ppg: number | null;
  trade_value: number | null;
  pos_rank: string | null;
  /** His game has kicked off: he can't be moved. */
  locked?: boolean;
  kickoff?: string | null;
}

export interface LineupChange {
  start: PlayerSummary;
  start_projection: number;
  bench: PlayerSummary | null;
  bench_projection: number | null;
  slot: string;
  gain: number;
  /** toss_up: under 1.5 points apart (about a coin flip); lean: 1.5-3; clear: more. */
  confidence?: 'toss_up' | 'lean' | 'clear';
}

export interface MyTeamResponse {
  league: MyLeague;
  week: number;
  roster: MyTeamRosterRow[];
  /** Risky starters: inactives timing and whether a backup plays later. */
  alerts?: string[];
  lineup: { current_total: number; best_total: number; changes: LineupChange[] };
}

export interface WaiverAdvice {
  action: 'bid' | 'pass' | 'claim' | 'wait' | 'add';
  note: string;
  bid?: number;
  market?: number;
  worth?: number;
  estimate?: boolean;
}

export interface LeagueWaiverCandidate extends WaiverCandidate {
  /** Points this pickup adds to your best lineup over the rest of the season (after the drop). */
  lineup_gain?: number;
  drop?: PlayerSummary | null;
  advice?: WaiverAdvice;
}

export interface LeagueWaiversResponse {
  league: string;
  season: number;
  week: number;
  league_type?: 'faab' | 'priority' | 'free_agency';
  weeks_left?: number;
  my_priority?: number | null;
  my_balance?: number | null;
  faab_budget?: number | null;
  trade_end_date?: string | null;
  results: LeagueWaiverCandidate[];
}

export interface LeagueTeamOffers {
  team: string | null;
  owner: string | null;
  needs: Position[];
  offers: (TradeOffer & { fills_their_need: Position[]; your_gain?: number; their_gain?: number })[];
}

export interface LeagueTradesResponse {
  league: string;
  perspective: string;
  trade_end_date?: string | null;
  teams: LeagueTeamOffers[];
}

export interface LeagueSyncResult {
  league_id: number;
  teams: number;
  roster_spots: number;
  unmatched_players: string[];
  untracked_players: string[];
}
