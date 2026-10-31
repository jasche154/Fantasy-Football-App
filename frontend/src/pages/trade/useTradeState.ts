import { useCallback, useMemo, useState } from 'react';
import { evaluateTrade } from '../../api/actions';
import { getPlayer } from '../../api/data';
import type { LeagueSettings, PlayerSummary, TradeResult, TradeSideKey } from '../../api/types';
import { useAsync, type AsyncResult } from '../../hooks/useAsync';
import { paths } from '../../router/routes';
import { navigate } from '../../router/useHashLocation';
import {
  addToSide,
  canEvaluate,
  EMPTY_TRADE,
  parseTradeQuery,
  playersInResult,
  rememberPlayers,
  removeFromSide,
  swapSides,
  tradeQueryParams,
  withLeague,
  type KnownPlayers,
  type TradeIds,
} from './tradeQuery';

/** An evaluation tagged with the trade it was computed for. */
export interface KeyedTradeResult {
  key: string;
  result: TradeResult;
}

export interface TradeState {
  ids: TradeIds;
  known: KnownPlayers;
  evaluation: AsyncResult<KeyedTradeResult>;
  /** The evaluation of exactly the current trade, or undefined while it is loading/failed. */
  currentResult: TradeResult | undefined;
  isReady: boolean;
  add: (side: TradeSideKey, player: PlayerSummary) => void;
  remove: (side: TradeSideKey, id: number) => void;
  swap: () => void;
  clear: () => void;
  /** Score on an imported league (undefined: the rosterless calculator). */
  setLeague: (league: number | undefined) => void;
}

function commit(ids: TradeIds): void {
  navigate(paths.trade, tradeQueryParams(ids), { replace: true });
}

function toSummary(player: PlayerSummary): PlayerSummary {
  return { id: player.id, name: player.name, position: player.position, team: player.team };
}

export function tradeKeyOf(ids: TradeIds): string {
  return `${ids.give.join(',')}|${ids.get.join(',')}|${ids.league ?? ''}`;
}

/**
 * Resolves names for ids the evaluation can't supply: a shared link with one side empty,
 * or a two-sided link whose evaluation failed. Unknown ids (404) simply stay unresolved.
 */
function useResolveMissing(
  ids: TradeIds,
  known: KnownPlayers,
  settings: LeagueSettings,
  remember: (players: readonly PlayerSummary[]) => void,
  hasEvaluationFailed: boolean,
): void {
  const missing = [...ids.give, ...ids.get].filter((id) => known[id] === undefined);
  const shouldResolve = (!canEvaluate(ids) || hasEvaluationFailed) && missing.length > 0;
  useAsync(
    shouldResolve
      ? async (signal) => {
          const profiles = await Promise.allSettled(missing.map((id) => getPlayer(id, settings, undefined, signal)));
          const found = profiles.flatMap((outcome) => (outcome.status === 'fulfilled' ? [toSummary(outcome.value.player)] : []));
          remember(found);
          return found.length;
        }
      : null,
    [missing.join(','), shouldResolve],
  );
}

/** Trade state lives in the hash query (`#/trade?give=1,2&get=3`) so every trade is shareable. */
export function useTradeState(query: Readonly<Record<string, string>>, settings: LeagueSettings): TradeState {
  const ids = useMemo(() => parseTradeQuery(query), [query]);
  const [known, setKnown] = useState<KnownPlayers>({});
  const remember = useCallback(
    (players: readonly PlayerSummary[]) => setKnown((current) => rememberPlayers(current, players)),
    [],
  );
  const isReady = canEvaluate(ids);
  const tradeKey = tradeKeyOf(ids);
  const evaluation = useAsync(
    isReady
      ? async (signal) => {
          const result = await evaluateTrade({ give: [...ids.give], get: [...ids.get], settings, league_id: ids.league }, signal);
          remember(playersInResult(result));
          return { key: tradeKey, result };
        }
      : null,
    [tradeKey, ids.league === undefined ? settings : null],   // a league brings its own settings
  );
  useResolveMissing(ids, known, settings, remember, evaluation.status === 'error');
  const isCurrent = evaluation.status === 'success' && evaluation.data.key === tradeKey;

  return {
    ids,
    known,
    evaluation,
    currentResult: isCurrent ? evaluation.data?.result : undefined,
    isReady,
    add: (side, player) => {
      remember([player]);
      commit(addToSide(ids, side, player.id));
    },
    remove: (side, id) => commit(removeFromSide(ids, side, id)),
    swap: () => commit(swapSides(ids)),
    clear: () => commit(withLeague(EMPTY_TRADE, ids.league)),
    setLeague: (league) => commit(withLeague(ids, league)),
  };
}
