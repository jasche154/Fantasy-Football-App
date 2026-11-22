/** Replay League: the league, players, waivers and trades screens (the shared pieces are in ./replay). */
import type {
  ReplayActionResult,
  ReplayGameRow,
  ReplayInjury,
  ReplayNews,
  ReplayPage,
  ReplayPagination,
  ReplayPhase,
  ReplayPlayerRef,
  ReplayPlayerRow,
  ReplayPositionFilter,
  ReplayRecord,
  ReplayTeamRef,
  ReplayUsage,
  ReplayVerdict,
} from './replay';

// ---------------- league ----------------

export interface ReplayScoreboard {
  week: number;
  played: boolean;
  playoff: boolean;
  results: ReplayGameRow[];
}

export interface ReplayStandingsRow extends ReplayTeamRef, ReplayRecord {
  seed: number;
  in_playoffs: boolean;
}

export interface ReplayStandings {
  results: ReplayStandingsRow[];
  regular_season_weeks: number;
  playoff_teams: number;
  /** The playoff weeks, in order. */
  playoff_weeks: number[];
  /** How many top seeds skip the first playoff round. */
  byes: number;
  seeds_final: boolean;
  /** One scoreboard per playoff week whose games are known. */
  bracket: ReplayScoreboard[];
  champion: ReplayTeamRef | null;
}

export interface ReplayScheduleRow {
  week: number;
  playoff: boolean;
  played: boolean;
  /** No game that week (a playoff bye, or out of the pool's pairings): opponent and result are null. */
  idle: boolean;
  /** A playoff opponent is null until it is known. */
  opponent: ReplayTeamRef | null;
  points: number | null;
  opponent_points: number | null;
  result: 'W' | 'L' | 'T' | null;
}

export interface ReplaySchedule {
  team: ReplayTeamRef;
  results: ReplayScheduleRow[];
}

export interface ReplayActivityQuery extends ReplayPagination {
  /** One event kind or several joined by commas ("add,drop"). */
  kind?: string;
}

// ---------------- players and waivers ----------------

export type ReplayPlayerStatus = 'rostered' | 'waivers' | 'free_agent';
export type ReplayStatusFilter = 'available' | 'waivers' | 'free_agent' | 'rostered' | 'all';
export type ReplayPlayerSort = 'value' | 'projection' | 'ros_ppg' | 'adp' | 'points' | 'name';

export interface ReplayWireRow extends ReplayPlayerRow {
  status: ReplayPlayerStatus;
  owner: ReplayTeamRef | null;
  /** Real points in weeks already played. */
  season_points: number;
  last_points: number | null;
  /** His last 3 games before the current week; null for K/DST or no games. */
  usage: ReplayUsage | null;
}

export interface ReplayPlayers extends ReplayPage<ReplayWireRow> {
  week: number;
}

export interface ReplayPlayersQuery extends ReplayPagination {
  position?: ReplayPositionFilter;
  search?: string;
  status?: ReplayStatusFilter;
  sort?: ReplayPlayerSort;
}

export interface ReplayPlayerWeek {
  week: number;
  played: boolean;
  team: string | null;
  opponent: string | null;
  bye: boolean;
  out: boolean;
  projection: number;
  points: number | null;
  ros_ppg: number;
  value: number;
  rank: number;
  pos_rank: string;
  expert_rank: number | null;
  news: ReplayNews | null;
  /** That week's tag. */
  injury: ReplayInjury | null;
  /** That week's own game (games 1); null when unplayed or he did not play. */
  usage: ReplayUsage | null;
}

/** One player: where he stands now and weeks 1..current. */
export interface ReplayPlayerDetail {
  player: ReplayWireRow;
  weeks: ReplayPlayerWeek[];
}

export interface ReplayClaim {
  id: number;
  order: number;
  add: ReplayPlayerRow;
  drop: ReplayPlayerRow | null;
  /** 0 outside FAAB. */
  bid: number;
}

export interface ReplayFaab {
  budget: number;
  /** The user's budget left. */
  left: number;
  /** Every team in waiver order. */
  teams: { team: ReplayTeamRef; left: number }[];
}

export interface ReplayClaims {
  phase: ReplayPhase;
  can_claim: boolean;
  /** The user's place in the waiver order. */
  priority: number | null;
  waiver_order: ReplayTeamRef[];
  results: ReplayClaim[];
  /** Null unless a FAAB league. */
  faab: ReplayFaab | null;
}

/** POST claims and add-drop body. */
export interface ReplayClaimBody {
  add: number;
  drop?: number | null;
  /** Claims in FAAB leagues only (whole dollars, 0..left); never sent otherwise or on add-drop. */
  bid?: number | null;
}

// ---------------- trades ----------------

export interface ReplayOffer {
  id: number;
  week: number;
  status: 'pending' | 'accepted' | 'declined' | 'expired' | 'countered';
  from_team: ReplayTeamRef;
  to_team: ReplayTeamRef;
  /** What from_team sends. */
  give: ReplayPlayerRef[];
  get: ReplayPlayerRef[];
  /** The offer this one answers (a counter), else null. */
  counter_to: number | null;
}

/** A CPU offer the user can answer: a preview verdict from the user's side, or a note saying why not. */
export interface ReplayPendingOffer extends ReplayOffer {
  verdict: ReplayVerdict | null;
  note: string | null;
}

export interface ReplayTrades {
  open: boolean;
  deadline_week: number;
  max_players: number;
  offers: ReplayPendingOffer[];
  /** Every other offer and trade, newest first. */
  results: ReplayOffer[];
}

/** POST trades/evaluate and trades/propose body (player ids). */
export interface ReplayTradeBody {
  to_team: number;
  give: number[];
  get: number[];
  /** The user's own drops when the deal overfills the roster. */
  drops?: number[];
}

/** POST trades/offers/{id}/counter body: the user's own deal (give = what the user sends) with the CPU that made the offer. */
export type ReplayCounterBody = Omit<ReplayTradeBody, 'to_team'>;

export interface ReplayEvaluateResult {
  verdict: ReplayVerdict;
}

export interface ReplayProposeResult extends ReplayActionResult {
  verdict: ReplayVerdict;
  executed: boolean;
  /** The CPU's counter offer after a decline (pending, for the user to answer), or null. */
  counter: ReplayOffer | null;
}

export interface ReplayRespondResult extends ReplayActionResult {
  accepted: boolean;
  /** The offer no longer stood: the reason is in `message`. */
  expired: boolean;
  message: string | null;
  verdict: ReplayVerdict | null;
}
