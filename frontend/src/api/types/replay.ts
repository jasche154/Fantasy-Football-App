/** Replay League (/api/replay): the shared pieces, the game, draft, team and matchup. The league-wide screens are in
 * ./replayLeague. Mirrors the docstring at the top of backend/services/replay_views.py. */
import type { Scoring } from './core';

export type ReplayPosition = 'QB' | 'RB' | 'WR' | 'TE' | 'K' | 'DST';
/** The `position` query param: one position or the RB/WR/TE group. */
export type ReplayPositionFilter = ReplayPosition | 'FLEX';
export type ReplaySlot = ReplayPosition | 'FLEX' | 'BN';
export type ReplayPhase = 'draft' | 'waivers' | 'open' | 'complete';
export type ReplayWaiverMode = 'wednesday' | 'none';
export type ReplayDifficulty = 'normal' | 'hard';
export type ReplayEventKind = 'draft' | 'add' | 'drop' | 'claim' | 'trade' | 'result' | 'playoffs' | 'champion';

// ---------------- shared pieces ----------------

export interface ReplayTeamRef {
  team_id: number;
  name: string;
  is_user: boolean;
}

export interface ReplayPlayerRef {
  id: number;
  name: string;
  position: ReplayPosition;
  /** His NFL team as known before the week shown. */
  team: string | null;
}

export interface ReplayPlayerRow {
  player: ReplayPlayerRef;
  /** NFL team code, "BYE" or null (unknown team, or draft time). */
  opponent: string | null;
  bye: boolean;
  out: boolean;
  /** That week's pre-game projection (0 on a bye or when out). */
  projection: number;
  /** Real points in that week; null when the week is unplayed or he did not play. */
  points: number | null;
  ros_ppg: number;
  /** What he counts for in a trade package. */
  value: number;
  /** Value over replacement (K, DST: 0). */
  trade_value: number;
  rank: number;
  /** e.g. "RB7". */
  pos_rank: string;
  adp: number | null;
  /** Real games and points a game BEFORE that week. */
  games: number;
  ppg: number;
  /** Traded in the last two weeks. */
  trade_locked: boolean;
}

export interface ReplayRecord {
  wins: number;
  losses: number;
  ties: number;
  rank: number;
  points_for: number;
  points_against: number;
  /** Final finish once the season is over, else null. */
  place: number | null;
}

export interface ReplaySide extends ReplayTeamRef {
  /** Null for a week not reached yet. */
  projected: number | null;
  points: number | null;
  record: ReplayRecord;
}

export type ReplayGameLabel = 'Semi-final' | 'Championship' | 'Third place' | 'Consolation';

export interface ReplayGameRow {
  home: ReplaySide;
  away: ReplaySide;
  /** team_id of the winner; null when unplayed or tied. */
  winner: number | null;
  label: ReplayGameLabel | null;
}

export interface ReplayEvent {
  id: number;
  week: number;
  kind: ReplayEventKind;
  team: ReplayTeamRef | null;
  text: string;
  players: ReplayPlayerRef[];
}

export interface ReplayPage<T> {
  total: number;
  limit: number;
  offset: number;
  results: T[];
}

export interface ReplayPagination {
  limit?: number;
  offset?: number;
}

export interface ReplayPricedPlayer {
  player: ReplayPlayerRef;
  value: number;
}

export type ReplayFairLabel = 'fair' | 'slight_win' | 'big_win' | 'slight_loss' | 'big_loss';

/** A trade judged from the user's side: "give" is always what the USER sends. */
export interface ReplayVerdict {
  accepted: boolean;
  reasons: string[];
  fair_label: ReplayFairLabel;
  value_give: number;
  value_get: number;
  /** (get - give) / the larger, a fraction; above 0 the user gets more. */
  percent: number;
  /** Season-weighted lineup points each side gains. */
  your_gain: number;
  their_gain: number;
  give: ReplayPricedPlayer[];
  get: ReplayPricedPlayer[];
  your_drops: ReplayPlayerRef[];
  their_drops: ReplayPlayerRef[];
  suggestion: string | null;
}

// ---------------- seasons and saved games ----------------

export interface ReplayReadiness {
  season: number;
  ready: boolean;
  has_stats: boolean;
  has_schedule: boolean;
  has_kdst: boolean;
  adp_players: number;
  projection_weeks: number;
  kickers: number;
  defenses: number;
}

export interface ReplayPrepareStatus {
  season: number;
  status: 'running' | 'done' | 'failed';
  step: string | null;
  error: string | null;
  updated_at?: string;
}

export interface ReplaySeasons {
  results: ReplayReadiness[];
  prepare: ReplayPrepareStatus | null;
}

export interface ReplayGameListing {
  id: number;
  name: string;
  season: number;
  phase: ReplayPhase;
  week: number;
  teams: number;
  scoring: Scoring;
  /** The champion's team name. */
  champion: string | null;
  created_at: string;
  updated_at: string;
}

export interface ReplayGames {
  results: ReplayGameListing[];
}

/** POST /games. Leave draft_slot out for a random slot. */
export interface ReplayCreateBody {
  season: number | 'random';
  name?: string;
  teams?: number;
  scoring?: Scoring;
  draft_slot?: number;
  waiver_mode?: ReplayWaiverMode;
  difficulty?: ReplayDifficulty;
  seed?: number;
}

// ---------------- the game ----------------

export interface ReplaySettings {
  teams: number;
  scoring: Scoring;
  /** {slot: how many start}. */
  starters: Partial<Record<Exclude<ReplaySlot, 'BN'>, number>>;
  bench: number;
  roster_size: number;
  draft_slot: number;
  waiver_mode: ReplayWaiverMode;
  difficulty: ReplayDifficulty;
  regular_season_weeks: number;
  playoff_teams: number;
  trade_deadline_week: number;
}

export type ReplayStep = 'pick' | 'wednesday' | 'play' | 'done';

export interface ReplayNextAction {
  step: ReplayStep;
  label: string;
}

/** The user's game this week (the final once the season is over). */
export interface ReplayHeadline {
  week: number;
  playoff: boolean;
  label: ReplayGameLabel | null;
  you: ReplaySide;
  opponent: ReplaySide;
}

export interface ReplaySummary {
  id: number;
  name: string;
  season: number;
  phase: ReplayPhase;
  /** The next week to be played; 0 during the draft. */
  week: number;
  end_week: number;
  user_team_id: number;
  settings: ReplaySettings;
  teams: ReplayTeamRef[];
  /** team_id on the clock in the draft, else null. */
  on_the_clock: number | null;
  next_action: ReplayNextAction;
  record: ReplayRecord;
  /** Null during the draft. */
  matchup: ReplayHeadline | null;
  pending: { claims: number; offers: number };
  champion: ReplayTeamRef | null;
  updated_at: string | null;
}

/** What every POST under /games answers; `events` is what the action caused, oldest first. */
export interface ReplayActionResult {
  game: ReplaySummary;
  events: ReplayEvent[];
}

export interface ReplayDeleted {
  deleted: number;
}

// ---------------- draft ----------------

export interface ReplayDraftRow {
  player: ReplayPlayerRef;
  adp: number | null;
  pos_rank: string;
  rank: number;
  projection: number;
}

export interface ReplayDraftPick {
  overall: number;
  round: number;
  team: ReplayTeamRef;
  player: ReplayPlayerRef;
}

/** Undrafted players in draft order (empty after the draft) plus the room's state. */
export interface ReplayDraft extends ReplayPage<ReplayDraftRow> {
  phase: ReplayPhase;
  on_the_clock: number | null;
  is_user_turn: boolean;
  /** The pick now on the clock; null when the draft is done. */
  round: number | null;
  overall: number | null;
  rounds: number;
  total_picks: number;
  order: ReplayTeamRef[];
  picks: ReplayDraftPick[];
  /** The user's picks by position. */
  roster: Record<ReplayPosition, ReplayPlayerRef[]>;
}

export interface ReplayDraftQuery extends ReplayPagination {
  position?: ReplayPositionFilter;
  search?: string;
}

// ---------------- team and matchup ----------------

export interface ReplayRosterRow extends ReplayPlayerRow {
  slot: ReplaySlot;
  starting: boolean;
}

/** A roster for one week: starters in slot order, then the bench (current week only). */
export interface ReplayTeam {
  team: ReplayTeamRef;
  week: number;
  played: boolean;
  manual_lineup: boolean;
  projected_total: number;
  actual_total: number | null;
  record: ReplayRecord;
  results: ReplayRosterRow[];
}

/** POST lineup body: {slot: player ids}; null goes back to the best lineup. */
export type ReplayLineup = Partial<Record<Exclude<ReplaySlot, 'BN'>, number[]>>;

export interface ReplayLineupResult extends ReplayActionResult {
  team: ReplayTeam;
}

export interface ReplayMatchup {
  week: number;
  played: boolean;
  playoff: boolean;
  label: ReplayGameLabel | null;
  winner: number | null;
  home: ReplayTeam;
  away: ReplayTeam;
}

export interface ReplayAdvanceResult extends ReplayActionResult {
  step: 'wednesday' | 'play';
  /** The week it acted on. */
  week: number;
}
