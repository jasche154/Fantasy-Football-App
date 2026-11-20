import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, errorMessage } from '../../api/client';
import { getReplayGame } from '../../api/replay';
import type { ReplayEvent, ReplaySummary } from '../../api/types';
import { useAsync, type AsyncResult } from '../../hooks/useAsync';
import type { ReplayAct } from './shared';

const STALE_MESSAGE = 'This league had already moved on, so nothing was changed. It has been reloaded: check where things stand.';

export interface GameState {
  /** The first load of the summary. */
  loaded: AsyncResult<ReplaySummary>;
  game: ReplaySummary | undefined;
  version: number;
  act: ReplayAct;
  isBusy: boolean;
  actError: string | null;
  /** What the last action caused, for the "What just happened" panel. */
  events: readonly ReplayEvent[];
  dismissEvents: () => void;
}

/** One game's summary plus `act`, the single path every mutation takes (see shared.tsx). */
export function useGame(gameId: number): GameState {
  const loaded = useAsync((signal) => getReplayGame(gameId, signal), [gameId]);
  const [latest, setLatest] = useState<ReplaySummary | null>(null);      // from the last act; newer than `loaded`
  const [version, setVersion] = useState(0);
  const [isBusy, setIsBusy] = useState(false);
  const [actError, setActError] = useState<string | null>(null);
  const [events, setEvents] = useState<readonly ReplayEvent[]>([]);
  const running = useRef(false);
  // Screens' useAsync only switch to 'loading' in an effect, so stay busy for the render that first shows a new version.
  const [settledVersion, setSettledVersion] = useState(0);
  useEffect(() => setSettledVersion(version), [version]);

  const act = useCallback<ReplayAct>(async (run) => {
    if (running.current) return undefined;
    running.current = true;
    setIsBusy(true);
    setActError(null);
    try {
      const result = await run();
      setLatest(result.game);
      setVersion((n) => n + 1);
      if (result.events.length > 0) setEvents(result.events);
      return result;
    } catch (error) {
      let message = errorMessage(error);
      if (error instanceof ApiError && error.status === 409) {
        // The summary on screen stays up until the new one is in, so no screen ever renders an older week.
        try {
          setLatest(await getReplayGame(gameId));
          setVersion((n) => n + 1);
          message = STALE_MESSAGE;
        } catch {
          // The reload failed too: the server's own 409 message ("... Reload it.") is the honest one to show.
        }
      }
      setActError(message);
      return undefined;
    } finally {
      running.current = false;
      setIsBusy(false);
    }
  }, [gameId]);

  const dismissEvents = useCallback(() => setEvents([]), []);
  return { loaded, game: latest ?? loaded.data, version, act, isBusy: isBusy || settledVersion !== version, actError, events, dismissEvents };
}
