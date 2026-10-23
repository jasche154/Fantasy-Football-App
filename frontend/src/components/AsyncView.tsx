import type { ReactNode } from 'react';
import type { AsyncResult } from '../hooks/useAsync';
import { EmptyState, ErrorState, Skeleton } from './StatusViews';

interface AsyncViewProps<T> {
  state: AsyncResult<T>;
  children: (data: T) => ReactNode;
  loading?: ReactNode;
  isEmpty?: (data: T) => boolean;
  empty?: ReactNode;
  errorTitle?: string;
  /** Custom error rendering (e.g. friendlier copy for a specific status code). */
  renderError?: (error: Error, retry: () => void) => ReactNode;
}

/**
 * Renders the right view for an async state: skeleton, error (with retry), empty, or
 * data. While a reload is in flight, the previous data stays on screen marked busy.
 */
export function AsyncView<T>({
  state,
  children,
  loading,
  isEmpty,
  empty,
  errorTitle,
  renderError,
}: AsyncViewProps<T>) {
  if (state.status === 'error') {
    return renderError ? (
      <>{renderError(state.error, state.reload)}</>
    ) : (
      <ErrorState error={state.error} title={errorTitle} onRetry={state.reload} />
    );
  }
  if (state.data === undefined) {
    return <>{state.status === 'idle' ? null : (loading ?? <Skeleton />)}</>;
  }
  const isRefreshing = state.status === 'loading';
  if (isEmpty?.(state.data)) {
    return <>{empty ?? <EmptyState title="Nothing to show yet" />}</>;
  }
  return (
    <div className={isRefreshing ? 'async-content is-refreshing' : 'async-content'} aria-busy={isRefreshing}>
      {children(state.data)}
    </div>
  );
}
