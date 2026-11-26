/** Replay League: the league, players, waivers and trades screens (the shared pieces are in ./replay). */
import type {
  ReplayActionResult,
  ReplayAdvKey,
  ReplayAdvLine,
  ReplayBoxLine,
  ReplayDefenseRating,
  ReplayGameRow,
  ReplayInjury,
  ReplayNews,
  ReplayPage,
  ReplayPagination,
  ReplayPhase,
  ReplayPlayerRef,
  ReplayPlayerRow,
  ReplayPosition,
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
}

/** A team in the playoff picture, with its seed (regular-season standings; provisional until seeds_final). */
export interface ReplaySeed extends ReplayTeamRef {
  seed: number;
}

export interface ReplayBracketSide extends ReplaySeed {
  /** Null until the week is reached / played. */
  projected: number | null;
  points: number | null;
}

/** One bracket slot: both teams null while they aren't decided. */
export interface ReplayBracketGame {
  label: string;
  home: ReplayBracketSide | null;
  away: ReplayBracketSide | null;
  winner: number | null;
}

export interface ReplayBracketRound {
  week: number;
  name: 'Quarter-finals' | 'Semi-finals' | 'Final';
  /** Round one drawn from the current seeds while the regular season is still going. */
  projected: boolean;
  /** Top seeds who skip this round (round one only). */
  byes: ReplaySeed[];
  /** The final round: the Championship, then the Third place game. */
  games: ReplayBracketGame[];
}

export interface ReplayBracketPool {
  name: string;
  /** The places this pool decides, e.g. [7, 12]. */
  places: [number, number];
  first_week: number;
  size: number;
  /** Null until known (the round-one losers). */
  teams: ReplaySeed[] | null;
  /** known: that week's pairs are set (drawn week by week from the pool's own results). */
  weeks: { week: number; known: boolean; games: ReplayBracketGame[] }[];
}

export interface ReplayPlayoffs {
  weeks: number[];
  seeds_final: boolean;
  champion: ReplayTeamRef | null;
  title: ReplayBracketRound[];
  pools: ReplayBracketPool[];
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
export type ReplayBoxKey = keyof ReplayBoxLine;
export type ReplayPlayerSort =
  | 'value'
  | 'projection'
  | 'ros_ppg'
  | 'adp'
  | 'points'
  | 'name'
  | 'ppg'
  | 'last'
  | 'line_points'
  | 'line_projection'
  | ReplayBoxKey
  | `adv_${ReplayAdvKey}`;
export type ReplaySortDir = 'asc' | 'desc';
export type ReplayStatsSpan = 'season' | 'last4' | 'week';

/** The stat columns of a wire row, for the chosen span. */
export interface ReplayStatLine {
  span: ReplayStatsSpan;
  /** Set for span "week". */
  week: number | null;
  games: number;
  /** season: total before now; last4: per-game average; week: null unless played and he played. */
  points: number | null;
  /** week span only: that week's pre-game number; else null. */
  projection: number | null;
  /** Same rules as points; null for K/DST. */
  box: ReplayBoxLine | null;
  /** Advanced rates, same span and rules as box. */
  adv: ReplayAdvLine | null;
}

export interface ReplayWireRow extends ReplayPlayerRow {
  status: ReplayPlayerStatus;
  /** On waivers in the open phase: dropped this week, claimable only after this week's games. */
  after_games?: boolean;
  owner: ReplayTeamRef | null;
  /** Real points in weeks already played. */
  season_points: number;
  last_points: number | null;
  /** His last 3 games before the current week; null for K/DST or no games. */
  usage: ReplayUsage | null;
  line: ReplayStatLine;
}

export interface ReplayPlayers extends ReplayPage<ReplayWireRow> {
  week: number;
  stats: ReplayStatsSpan;
  stat_week: number | null;
}

export interface ReplayPlayersQuery extends ReplayPagination {
  position?: ReplayPositionFilter;
  search?: string;
  status?: ReplayStatusFilter;
  sort?: ReplayPlayerSort;
  /** Default asc for name/adp, desc otherwise; nulls always last. */
  dir?: ReplaySortDir;
  stats?: ReplayStatsSpan;
  /** 1..current; only with stats "week". */
  stat_week?: number;
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
  /** That week's own box score; null when unplayed, he did not play, or K/DST. */
  box: ReplayBoxLine | null;
  defense: ReplayDefenseRating | null;
  away: boolean;
}

/** One player's line in a played NFL game (GET .../box). box: null for K and DST. */
export interface ReplayBoxPlayer {
  id: number;
  name: string;
  position: ReplayPosition;
  /** Fantasy points under the league's scoring. */
  points: number;
  box: ReplayBoxLine | null;
}

/** One side of a played NFL game. score: null when the season doesn't have it (an older league). */
export interface ReplayBoxTeam {
  team: string;
  home: boolean;
  score: number | null;
  players: ReplayBoxPlayer[];
}

/** A played NFL game's box score: the away team first, then the home team. */
export interface ReplayBoxScore {
  week: number;
  teams: [ReplayBoxTeam, ReplayBoxTeam];
}

/** A week after the current one: an estimate, never a projection. */
export interface ReplayUpcomingWeek {
  week: number;
  opponent: string | null;
  away: boolean;
  bye: boolean;
  /** ros_ppg x the defense factor (1 decimal); null on a bye or when he is expected out. */
  estimate: number | null;
  defense: ReplayDefenseRating | null;
  out_expected: boolean;
}

/** One player: where he stands now, weeks 1..current and the weeks after. */
export interface ReplayPlayerDetail {
  player: ReplayWireRow;
  weeks: ReplayPlayerWeek[];
  upcoming: ReplayUpcomingWeek[];
  /** What the matchup ratings rest on: weeks of this season and whether last season is blended in. */
  defense_basis: { weeks: number; prior: boolean };
  /** Every news line (rumour) about him known so far, newest first; [] in a real replay. */
  buzz: { week: number; text: string }[];
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
  /** The week the page shows: 409 when the league has moved on. */
  week?: number;
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
  /** A CPU offer's one line to the user, in his voice; null otherwise. */
  message: string | null;
  /** The CPU's one line when it turned the user's proposal down; null otherwise. */
  reply: string | null;
}

/** A player a worried CPU is shopping to the user this week (a little under his usual price), with his message. */
export interface ReplayShop {
  team: ReplayTeamRef;
  player: ReplayPlayerRef;
  message: string | null;
}

export type ReplayChatKind =
  | 'blowout_win' | 'close_win' | 'close_loss' | 'blowout_loss' | 'boom' | 'bust' | 'streak_win' | 'streak_loss'
  | 'upset' | 'clinch' | 'eliminated' | 'champion' | 'runner_up' | 'trade_reaction' | 'shop';

/** One line in the league chat (the CPUs talk, the user reads). */
export interface ReplayChat {
  id: number;
  week: number;
  team: ReplayTeamRef;
  /** Addressed to the user: his opponent's line, or a manager shopping him a player. */
  to_user: boolean;
  kind: ReplayChatKind;
  text: string;
  players: ReplayPlayerRef[];
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
  /** Players CPUs are shopping to the user now; empty once trades close. */
  shops: ReplayShop[];
}

/** POST trades/evaluate and trades/propose body (player ids). */
export interface ReplayTradeBody {
  to_team: number;
  give: number[];
  get: number[];
  /** The user's own drops when the deal overfills the roster. */
  drops?: number[];
  /** The week the page shows (propose and counter): 409 when the league has moved on. */
  week?: number;
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
  /** The CPU's one line on why it turned the deal down, or null. */
  reply: string | null;
}

export interface ReplayRespondResult extends ReplayActionResult {
  accepted: boolean;
  /** The offer no longer stood: the reason is in `message`. */
  expired: boolean;
  message: string | null;
  verdict: ReplayVerdict | null;
  /** After declining a haggler's opening offer: his better offer (pending), else null. */
  counter: ReplayOffer | null;
}
