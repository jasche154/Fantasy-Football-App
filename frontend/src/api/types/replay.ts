/** Replay League (/api/replay): the shared pieces, the game, draft, team and matchup. The league-wide screens are in
 * ./replayLeague. Mirrors the docstring at the top of backend/services/replay_views.py. */
import type { Scoring } from './core';

export type ReplayPosition = 'QB' | 'RB' | 'WR' | 'TE' | 'K' | 'DST';
/** The `position` query param: one position or the RB/WR/TE group. */
export type ReplayPositionFilter = ReplayPosition | 'FLEX';
export type ReplaySlot = ReplayPosition | 'FLEX' | 'SUPERFLEX' | 'BN';
/** {slot: how many start}; SUPERFLEX takes a QB, RB, WR or TE. */
export type ReplayStarters = Partial<Record<Exclude<ReplaySlot, 'BN'>, number>>;
/** A league's custom key settings on top of its scoring preset (backend/services/replay_scoring.py). */
export interface ReplayScoringRules {
  reception: number;
  te_premium: number;
  pass_td: number;
  /** INT and fumble lost. */
  turnover: number;
}
export type ReplayPhase = 'draft' | 'waivers' | 'open' | 'complete';
export type ReplayWaiverMode = 'wednesday' | 'faab' | 'none';
export type ReplayDifficulty = 'normal' | 'hard';
export type ReplayEventKind = 'draft' | 'add' | 'drop' | 'claim' | 'trade' | 'result' | 'playoffs' | 'champion' | 'ir';
/** O, IR, PUP and SUS mean he does not play; O, IR and PUP may go on IR. */
export type ReplayInjuryTag = 'Q' | 'D' | 'O' | 'IR' | 'PUP' | 'SUS';
export type ReplayPractice = 'DNP' | 'LP' | 'FP';

export interface ReplayInjury {
  tag: ReplayInjuryTag;
  practice: ReplayPractice | null;
  injury: string | null;
}

// ---------------- shared pieces ----------------

export interface ReplayTeamRef {
  team_id: number;
  name: string;
  is_user: boolean;
  /** A one-line hint at how a CPU trades (its persona stays hidden); null for the user and before 3 weeks. */
  tendency: string | null;
}

export interface ReplayPlayerRef {
  id: number;
  name: string;
  position: ReplayPosition;
  /** His NFL team as known before the week shown. */
  team: string | null;
  /** His tag in the current week (a row built for another week: that week's); always null in the draft room. */
  injury: ReplayInjury | null;
}

/** One game's (or a sum or average of games') box score; K/DST have none. Whole numbers, averages 1 decimal. */
export interface ReplayBoxLine {
  pass_yds: number;
  pass_td: number;
  pass_int: number;
  carries: number;
  rush_yds: number;
  rush_td: number;
  targets: number;
  rec: number;
  rec_yds: number;
  rec_td: number;
}

/** Advanced rates over a stat span's games; each null when its denominator is 0 or its data is missing.
 *  Percents are 0-100, everything 1 decimal except td_int (2). */
export interface ReplayAdvLine {
  /** Mean snap % over the games that have snap counts. */
  snap_pct: number | null;
  /** Mean share of his team's targets, 0-100. */
  target_share: number | null;
  targets_pg: number | null;
  carries_pg: number | null;
  /** Carries + receptions per game. */
  touches_pg: number | null;
  ypc: number | null;
  ypr: number | null;
  ypt: number | null;
  catch_rate: number | null;
  /** Rushing + receiving + passing TDs: the span's total (per game for last4). */
  total_td: number | null;
  /** Rushing + receiving TDs per touch, 0-100. */
  td_rate: number | null;
  /** Null without a pass thrown. */
  pass_ypg: number | null;
  /** Pass TD / INT; just the TDs when he threw no INT; null without a TD or INT. */
  td_int: number | null;
}
export type ReplayAdvKey = keyof ReplayAdvLine;

export type ReplayDefenseTier = 'easy' | 'neutral' | 'tough';

/** How much an NFL defense gives up to one position, as known before the week. */
export interface ReplayDefenseRating {
  /** 1 = gives up the most to that position. */
  rank: number;
  of: number;
  /** easy = top quarter by rank, tough = bottom quarter. */
  tier: ReplayDefenseTier;
  /** Opponent-adjusted multiplier, about 0.8-1.2. */
  factor: number;
  /** League average points allowed to the position x factor (1 decimal). */
  allowed: number;
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
  /** His 0-1000 trade chart value this week (after injury), a whole number. */
  value: number;
  /** By chart value. */
  rank: number;
  /** e.g. "RB7". */
  pos_rank: string;
  adp: number | null;
  /** Overall preseason rank (experts interleaved on ADP, else ADP order). */
  pre_rank: number | null;
  /** This week's expert position rank; null when unranked. */
  expert_rank: number | null;
  /** The injury report while he is out; it can be wrong. */
  news: ReplayNews | null;
  /** Real games and points a game BEFORE that week. */
  games: number;
  ppg: number;
  /** Traded in the last two weeks. */
  trade_locked: boolean;
  /** That week's opponent vs his position; null for K/DST, a bye, an unknown team or nothing known. */
  defense: ReplayDefenseRating | null;
}

/** An injury report: back = the week he is said to return (null = out for the season). */
export interface ReplayNews {
  back: number | null;
  text: string;
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

/** Title games are named by round; every game for places outside the title (5th and down, or out of the playoffs) is 'Consolation'. */
export type ReplayGameLabel = 'Quarter-final' | 'Semi-final' | 'Championship' | 'Third place' | 'Consolation';

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

/** Usage averaged over `games` games: snap_pct 0-100, targets and carries per game, target_share a 0-1 fraction. */
export interface ReplayUsage {
  games: number;
  snap_pct: number | null;
  targets: number;
  target_share: number | null;
  carries: number;
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
  /** Weeks with injury tags stored (`ready` does not need it). */
  injury_weeks: number;
  /** The season's last week with games (17 or 18): a week-18 championship needs 18. */
  last_week: number;
}

export interface ReplayPrepareStatus {
  season: number;
  status: 'running' | 'done' | 'failed';
  /** The step running or failed, e.g. 'snaps' or 'experts'. */
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
  /** The championship week; the regular season is what the playoff rounds leave before it. Default 17. */
  playoff_end_week?: ReplayPlayoffEndWeek;
  /** 4..min(8, teams); left out: the default for the league size. */
  playoff_teams?: number;
  seed?: number;
  /** Left out: the standard lineup. */
  starters?: ReplayStarters;
  /** Left out: the scoring preset as is. */
  scoring_rules?: ReplayScoringRules;
}

export type ReplayPlayoffEndWeek = 16 | 17 | 18;

// ---------------- the game ----------------

export interface ReplaySettings {
  teams: number;
  scoring: Scoring;
  /** {slot: how many start}. */
  starters: Partial<Record<Exclude<ReplaySlot, 'BN'>, number>>;
  bench: number;
  roster_size: number;
  /** IR spots per team: a player traded off IR takes a free one, else a roster spot. */
  ir_slots: number;
  draft_slot: number;
  waiver_mode: ReplayWaiverMode;
  difficulty: ReplayDifficulty;
  regular_season_weeks: number;
  playoff_teams: number;
  trade_deadline_week: number;
  /** null: the scoring preset as is. Optional: answers saved before custom scoring. */
  scoring_rules?: ReplayScoringRules | null;
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
  /** The user's IR players who lost their tag: adds, claims and trades are refused while non-empty. */
  ir_blocked: ReplayPlayerRef[];
  /** Injury tags are loaded for this season. */
  has_injuries: boolean;
  /** Trades can be made now: through the deadline week and its waiver phase. */
  trades_open: boolean;
  /** What this week's chart values lean on: expert ranks, or the app's model when none are loaded. */
  rank_source: 'experts' | 'model';
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
  /** Overall preseason rank (experts interleaved on ADP, else ADP order); null when unranked. */
  pre_rank: number | null;
  /** Draft round his preseason rank implies; null when unranked. */
  round: number | null;
  /** Raw preseason points a game. */
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

export type ReplayDraftSort = 'rank' | 'adp' | 'projection';

export interface ReplayDraftQuery extends ReplayPagination {
  position?: ReplayPositionFilter;
  search?: string;
  /** Default rank; unranked players (nulls) always come last. */
  sort?: ReplayDraftSort;
  /** Default asc for rank and adp, desc for projection. */
  direction?: 'asc' | 'desc';
}

// ---------------- team and matchup ----------------

export interface ReplayRosterRow extends ReplayPlayerRow {
  slot: ReplaySlot;
  starting: boolean;
}

export interface ReplayIrRow extends ReplayPlayerRow {
  slot: 'IR';
  starting: false;
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
  /** Starters then bench; a played week holds its bench rows only when `bench_recorded`. */
  results: ReplayRosterRow[];
  /** Real points left on the bench; null when unplayed or not recorded. */
  bench_total: number | null;
  /** False for weeks played before the bench was saved. */
  bench_recorded: boolean;
  /** The IR slot: current week only, else []. Never in `results`. */
  ir: ReplayIrRow[];
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
