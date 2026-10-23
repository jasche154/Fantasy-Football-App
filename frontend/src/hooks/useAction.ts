import { useCallback, useEffect, useRef, useState } from 'react';

export type ActionState<T> =
  | { status: 'idle' }
  | { status: 'pending' }
  | { status: 'success'; data: T }
  | { status: 'error'; error: Error };

export type ActionResult<A extends unknown[], T> = ActionState<T> & {
  /** Runs the action; resolves with the data, or undefined when it failed (see `error`). */
  run: (...args: A) => Promise<T | undefined>;
  reset: () => void;
};

/** State machine for user-triggered requests (POSTs, button clicks). */
export function useAction<A extends unknown[], T>(action: (...args: A) => Promise<T>): ActionResult<A, T> {
  const [state, setState] = useState<ActionState<T>>({ status: 'idle' });
  const isMounted = useRef(true);

  useEffect(() => {
    isMounted.current = true;
    return () => {
      isMounted.current = false;
    };
  }, []);

  const run = useCallback(
    async (...args: A): Promise<T | undefined> => {
      setState({ status: 'pending' });
      try {
        const data = await action(...args);
        if (isMounted.current) setState({ status: 'success', data });
        return data;
      } catch (error) {
        if (isMounted.current) {
          setState({ status: 'error', error: error instanceof Error ? error : new Error(String(error)) });
        }
        return undefined;
      }
    },
    [action],
  );

  const reset = useCallback(() => setState({ status: 'idle' }), []);
  return { ...state, run, reset };
}
