import type { AsyncResult } from '../hooks/useAsync';

/**
 * Screen-reader-only announcement of an async list's status ("Loading…", "25 players shown").
 * Pair it with `isAnnounced={false}` on the skeleton so loading isn't read twice.
 */
export function LiveStatus<T>({
  state,
  describe,
  loadingText = 'Loading…',
}: {
  state: AsyncResult<T>;
  describe: (data: T) => string;
  loadingText?: string;
}) {
  // Errors are announced by the ErrorState alert itself, so stay quiet here.
  let message = '';
  if (state.status === 'loading') message = loadingText;
  else if (state.status === 'success') message = describe(state.data);
  return (
    <p className="sr-only" aria-live="polite" aria-atomic="true">
      {message}
    </p>
  );
}

export function countLabel(count: number, noun = 'player'): string {
  return `${count} ${count === 1 ? noun : `${noun}s`} shown.`;
}
