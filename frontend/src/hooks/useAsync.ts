import { useCallback, useEffect, useState, type DependencyList } from 'react';
import { isAbortError } from '../api/client';

export type AsyncState<T> =
  | { status: 'idle'; data?: undefined; error?: undefined }
  | { status: 'loading'; data?: T; error?: undefined }
  | { status: 'success'; data: T; error?: undefined }
  | { status: 'error'; data?: T; error: Error };

export type AsyncResult<T> = AsyncState<T> & { reload: () => void };

export type Loader<T> = (signal: AbortSignal) => Promise<T>;

function toError(error: unknown): Error {
  return error instanceof Error ? error : new Error(String(error));
}

/**
 * Runs `loader` whenever `deps` change (or `reload()` is called), aborting the previous
 * request. Pass `null` to stay idle. Previous data is kept while a reload is in flight
 * so tables don't flash empty.
 */
export function useAsync<T>(loader: Loader<T> | null, deps: DependencyList): AsyncResult<T> {
  const [state, setState] = useState<AsyncState<T>>(() =>
    loader ? { status: 'loading' } : { status: 'idle' },
  );
  const [attempt, setAttempt] = useState(0);
  const isEnabled = loader !== null;

  useEffect(() => {
    if (!loader) {
      setState({ status: 'idle' });
      return undefined;
    }
    const controller = new AbortController();
    setState((previous) => ({ status: 'loading', data: previous.data }));
    loader(controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) setState({ status: 'success', data });
      },
      (error: unknown) => {
        if (controller.signal.aborted || isAbortError(error)) return;
        setState((previous) => ({ status: 'error', data: previous.data, error: toError(error) }));
      },
    );
    return () => controller.abort();
    // `loader` is intentionally excluded: callers pass an inline closure and list its
    // inputs in `deps`, the same contract as useEffect.
  }, [...deps, isEnabled, attempt]);

  const reload = useCallback(() => setAttempt((value) => value + 1), []);
  return { ...state, reload };
}
