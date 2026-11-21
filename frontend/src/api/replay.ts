/** Replay League endpoints (/api/replay): one function per route. Every change is a POST. */
import { apiRequest } from './client';
import type {
  ReplayActionResult,
  ReplayActivityQuery,
  ReplayAdvanceResult,
  ReplayClaimBody,
  ReplayClaims,
  ReplayCreateBody,
  ReplayDeleted,
  ReplayDraft,
  ReplayDraftQuery,
  ReplayEvaluateResult,
  ReplayEvent,
  ReplayGames,
  ReplayLineup,
  ReplayLineupResult,
  ReplayMatchup,
  ReplayPage,
  ReplayPlayerDetail,
  ReplayPlayers,
  ReplayPlayersQuery,
  ReplayPrepareStatus,
  ReplayProposeResult,
  ReplayRespondResult,
  ReplaySchedule,
  ReplayScoreboard,
  ReplaySeasons,
  ReplayStandings,
  ReplaySummary,
  ReplayTeam,
  ReplayTradeBody,
  ReplayTrades,
} from './types';

const ROOT = '/api/replay';
const game = (gameId: number): string => `${ROOT}/games/${gameId}`;

function post<T>(path: string, body?: unknown, query?: Record<string, boolean | number | undefined>): Promise<T> {
  return apiRequest(path, { method: 'POST', body, query });
}

// ---------------- seasons and saved games ----------------

export function getReplaySeasons(signal?: AbortSignal): Promise<ReplaySeasons> {
  return apiRequest(`${ROOT}/seasons`, { signal });
}

/** Starts the import in the background; poll getReplaySeasons. Answers the import now running. */
export function prepareReplaySeason(season: number, force?: boolean): Promise<ReplayPrepareStatus> {
  return post(`${ROOT}/seasons/${season}/prepare`, undefined, { force });
}

export function getReplayGames(signal?: AbortSignal): Promise<ReplayGames> {
  return apiRequest(`${ROOT}/games`, { signal });
}

export function createReplayGame(body: ReplayCreateBody): Promise<ReplayActionResult> {
  return post(`${ROOT}/games`, body);
}

export function getReplayGame(gameId: number, signal?: AbortSignal): Promise<ReplaySummary> {
  return apiRequest(game(gameId), { signal });
}

export function deleteReplayGame(gameId: number): Promise<ReplayDeleted> {
  return post(`${game(gameId)}/delete`);
}

// ---------------- screens ----------------

export function getReplayDraft(gameId: number, query: ReplayDraftQuery = {}, signal?: AbortSignal): Promise<ReplayDraft> {
  return apiRequest(`${game(gameId)}/draft`, { query: { ...query }, signal });
}

/** A roster for a week (default: the user's, now). */
export function getReplayTeam(
  gameId: number,
  query: { team_id?: number; week?: number } = {},
  signal?: AbortSignal,
): Promise<ReplayTeam> {
  return apiRequest(`${game(gameId)}/team`, { query, signal });
}

/** Both lineups of one game (default: the user's game this week). */
export function getReplayMatchup(
  gameId: number,
  query: { week?: number; team_id?: number } = {},
  signal?: AbortSignal,
): Promise<ReplayMatchup> {
  return apiRequest(`${game(gameId)}/matchup`, { query, signal });
}

export function getReplayScoreboard(gameId: number, week?: number, signal?: AbortSignal): Promise<ReplayScoreboard> {
  return apiRequest(`${game(gameId)}/scoreboard`, { query: { week }, signal });
}

export function getReplayStandings(gameId: number, signal?: AbortSignal): Promise<ReplayStandings> {
  return apiRequest(`${game(gameId)}/standings`, { signal });
}

export function getReplaySchedule(gameId: number, teamId?: number, signal?: AbortSignal): Promise<ReplaySchedule> {
  return apiRequest(`${game(gameId)}/schedule`, { query: { team_id: teamId }, signal });
}

/** The league's log, newest first. */
export function getReplayActivity(
  gameId: number,
  query: ReplayActivityQuery = {},
  signal?: AbortSignal,
): Promise<ReplayPage<ReplayEvent>> {
  return apiRequest(`${game(gameId)}/activity`, { query: { ...query }, signal });
}

/** The waiver wire: the whole pool, filtered and paged. */
export function getReplayPlayers(gameId: number, query: ReplayPlayersQuery = {}, signal?: AbortSignal): Promise<ReplayPlayers> {
  return apiRequest(`${game(gameId)}/players`, { query: { ...query }, signal });
}

export function getReplayPlayer(gameId: number, playerId: number, signal?: AbortSignal): Promise<ReplayPlayerDetail> {
  return apiRequest(`${game(gameId)}/players/${playerId}`, { signal });
}

export function getReplayClaims(gameId: number, signal?: AbortSignal): Promise<ReplayClaims> {
  return apiRequest(`${game(gameId)}/claims`, { signal });
}

export function getReplayTrades(gameId: number, signal?: AbortSignal): Promise<ReplayTrades> {
  return apiRequest(`${game(gameId)}/trades`, { signal });
}

// ---------------- actions ----------------

/**
 * The user's pick; `events` lists the CPU picks up to the user's next turn. `overall` is the pick the board
 * shows on the clock: 409 when stale.
 */
export function replayDraftPick(gameId: number, playerId: number, overall?: number | null): Promise<ReplayActionResult> {
  return post(`${game(gameId)}/draft/pick`, { player_id: playerId, ...(overall != null && { overall }) });
}

/** Auto-pick for the user: this pick, or every pick left with `wholeDraft`. `overall` as in replayDraftPick. */
export function replayDraftAuto(gameId: number, wholeDraft?: boolean, overall?: number | null): Promise<ReplayActionResult> {
  return post(`${game(gameId)}/draft/auto`, undefined, { whole_draft: wholeDraft, overall: overall ?? undefined });
}

/** `starters` null: back to the best lineup. */
export function setReplayLineup(gameId: number, starters: ReplayLineup | null): Promise<ReplayLineupResult> {
  return post(`${game(gameId)}/lineup`, { starters });
}

export function submitReplayClaim(gameId: number, body: ReplayClaimBody): Promise<ReplayActionResult> {
  return post(`${game(gameId)}/claims`, body);
}

export function cancelReplayClaim(gameId: number, claimId: number): Promise<ReplayActionResult> {
  return post(`${game(gameId)}/claims/${claimId}/cancel`);
}

/** Change a pending FAAB claim's bid (whole dollars). */
export function setReplayClaimBid(gameId: number, claimId: number, bid: number): Promise<ReplayActionResult> {
  return post(`${game(gameId)}/claims/${claimId}/bid`, { bid });
}

/** `ids`: every pending claim id in the new preference order. */
export function reorderReplayClaims(gameId: number, ids: number[]): Promise<ReplayActionResult> {
  return post(`${game(gameId)}/claims/reorder`, { ids });
}

/** Add a free agent now, dropping `drop` when the roster is full. */
export function replayAddDrop(gameId: number, body: ReplayClaimBody): Promise<ReplayActionResult> {
  return post(`${game(gameId)}/add-drop`, body);
}

export function replayDrop(gameId: number, playerId: number): Promise<ReplayActionResult> {
  return post(`${game(gameId)}/drop`, { player_id: playerId });
}

/** Moves an active player tagged O, IR or PUP into the IR slot. */
export function replayIrPlace(gameId: number, playerId: number): Promise<ReplayActionResult> {
  return post(`${game(gameId)}/ir/place`, { player_id: playerId });
}

/** Brings a player back from IR; `drop` makes room on a full roster, `swap` (an injured active player) takes the slot instead. */
export function replayIrActivate(gameId: number, playerId: number, options: { drop?: number; swap?: number } = {}): Promise<ReplayActionResult> {
  return post(`${game(gameId)}/ir/activate`, { player_id: playerId, ...options });
}

/** "wednesday" runs the waivers, "play" scores the week. `week` is the week on screen: 409 when stale. */
export function advanceReplay(gameId: number, step: 'wednesday' | 'play', week: number): Promise<ReplayAdvanceResult> {
  return post(`${game(gameId)}/advance`, { step, week });
}

/** Would that CPU take it? Changes nothing. */
export function evaluateReplayTrade(gameId: number, body: ReplayTradeBody, signal?: AbortSignal): Promise<ReplayEvaluateResult> {
  return apiRequest(`${game(gameId)}/trades/evaluate`, { method: 'POST', body, signal });
}

/** Carried out at once when the CPU accepts (`executed`), otherwise the verdict says why not. */
export function proposeReplayTrade(gameId: number, body: ReplayTradeBody): Promise<ReplayProposeResult> {
  return post(`${game(gameId)}/trades/propose`, body);
}

export function respondReplayOffer(
  gameId: number,
  offerId: number,
  accept: boolean,
  drops: number[] = [],
): Promise<ReplayRespondResult> {
  return post(`${game(gameId)}/trades/offers/${offerId}/respond`, { accept, drops });
}
