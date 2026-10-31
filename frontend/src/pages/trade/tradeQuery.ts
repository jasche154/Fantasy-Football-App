import type { PlayerSummary, TradeResult, TradeSideKey } from '../../api/types';
import type { QueryParams } from '../../lib/query';
import { parsePositiveInt } from '../../router/routes';

export interface TradeIds {
  give: readonly number[];
  get: readonly number[];
  /** Imported league to score the trade on (its rosters, settings and lineup gains). */
  league?: number;
}

export const EMPTY_TRADE: TradeIds = { give: [], get: [] };

/** Keeps shared links from fanning out into hundreds of requests. */
export const MAX_PLAYERS_PER_SIDE = 10;

/** "1, 2,abc,2,3" -> [1, 2, 3]: positive integers only, de-duplicated, order kept, capped. */
export function parseIdList(raw: string | undefined): number[] {
  if (!raw) return [];
  const ids = raw
    .split(',')
    .map((part) => parsePositiveInt(part.trim()))
    .filter((id): id is number => id !== null);
  return [...new Set(ids)].slice(0, MAX_PLAYERS_PER_SIDE);
}

/** Reads `give` / `get` from the hash query. A player can only be on one side (give wins). */
export function parseTradeQuery(query: Readonly<Record<string, string>>): TradeIds {
  const give = parseIdList(query.give);
  const get = parseIdList(query.get).filter((id) => !give.includes(id));
  const league = parsePositiveInt(query.league ?? '');
  return league === null ? { give, get } : { give, get, league };
}

export function tradeQueryParams(ids: TradeIds): QueryParams {
  return {
    give: ids.give.length > 0 ? ids.give.join(',') : undefined,
    get: ids.get.length > 0 ? ids.get.join(',') : undefined,
    league: ids.league !== undefined ? String(ids.league) : undefined,
  };
}

export function withLeague(ids: TradeIds, league: number | undefined): TradeIds {
  return league === undefined ? { give: ids.give, get: ids.get } : { ...ids, league };
}

export function hasPlayer(ids: TradeIds, id: number): boolean {
  return ids.give.includes(id) || ids.get.includes(id);
}

export function addToSide(ids: TradeIds, side: TradeSideKey, id: number): TradeIds {
  if (hasPlayer(ids, id) || ids[side].length >= MAX_PLAYERS_PER_SIDE) return ids;
  return { ...ids, [side]: [...ids[side], id] };
}

export function removeFromSide(ids: TradeIds, side: TradeSideKey, id: number): TradeIds {
  return { ...ids, [side]: ids[side].filter((existing) => existing !== id) };
}

export function swapSides(ids: TradeIds): TradeIds {
  return { ...ids, give: ids.get, get: ids.give };
}

export function canEvaluate(ids: TradeIds): boolean {
  return ids.give.length > 0 && ids.get.length > 0;
}

export type KnownPlayers = Readonly<Record<number, PlayerSummary>>;

export function rememberPlayers(known: KnownPlayers, players: readonly PlayerSummary[]): KnownPlayers {
  const additions = players.filter((player) => known[player.id] === undefined);
  if (additions.length === 0) return known;
  return { ...known, ...Object.fromEntries(additions.map((player) => [player.id, player])) };
}

/** Every player named in an evaluation (both sides plus balance suggestions). */
export function playersInResult(result: TradeResult): PlayerSummary[] {
  return [
    ...result.give.players.map((entry) => entry.player),
    ...result.get.players.map((entry) => entry.player),
    ...result.balance_suggestions.map((suggestion) => suggestion.player),
  ];
}
