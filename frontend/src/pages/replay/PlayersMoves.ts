import { useEffect, useRef, useState, type RefObject } from 'react';
import type { ReplayActionResult } from '../../api/types';
import { useKeyedAct, type KeyedAct, type ReplayAct } from './shared';

export interface PlayerMoves extends Pick<KeyedAct, 'isBusy' | 'pendingKey' | 'errorFor'> {
  /**
   * Sends one roster move through the hub's `act`. `key` names the button that was pressed, `done` is said
   * once it went through. Resolves true on success. `keepFocus`: the pressed button stays on screen.
   */
  run: (key: string, request: () => Promise<ReplayActionResult>, done: string, keepFocus?: boolean) => Promise<boolean>;
  notice: string;
  /** Put on the element showing `notice`: it takes focus when the pressed button is about to go away. */
  noticeRef: RefObject<HTMLParagraphElement | null>;
}

/** The Players screen's moves: the shared keyed `act`, plus a confirmation that can take the keyboard focus. */
export function usePlayerMoves(act: ReplayAct, isHubBusy: boolean): PlayerMoves {
  const { send, isBusy, pendingKey, errorFor } = useKeyedAct(act, isHubBusy);
  const [notice, setNotice] = useState<{ text: string; keepFocus: boolean } | null>(null);
  const noticeRef = useRef<HTMLParagraphElement>(null);

  // An added player's row, a cancelled claim and the dialog all disappear: keep keyboard focus somewhere.
  useEffect(() => {
    if (notice && !notice.keepFocus) noticeRef.current?.focus({ preventScroll: true });
  }, [notice]);

  const run: PlayerMoves['run'] = async (key, request, done, keepFocus = false) => {
    setNotice(null);
    const result = await send(key, request);
    if (result) setNotice({ text: done, keepFocus });
    return result !== undefined;
  };

  return { run, isBusy, pendingKey, errorFor, noticeRef, notice: notice?.text ?? '' };
}
