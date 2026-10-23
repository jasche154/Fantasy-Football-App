import type { ReactNode } from 'react';
import { errorMessage } from '../api/client';
import { Icon } from './Icon';

interface ErrorStateProps {
  error: unknown;
  title?: string;
  onRetry?: () => void;
  /** Overrides the message derived from the error. */
  message?: ReactNode;
}

export function ErrorState({ error, title = "Couldn't load this data", onRetry, message }: ErrorStateProps) {
  return (
    <div className="state state--error" role="alert">
      <Icon name="alert" className="state__icon" />
      <div className="state__body">
        <p className="state__title">{title}</p>
        <p className="state__text">{message ?? errorMessage(error)}</p>
        {onRetry && (
          <button type="button" className="btn btn--ghost btn--sm" onClick={onRetry}>
            Try again
          </button>
        )}
      </div>
    </div>
  );
}

interface EmptyStateProps {
  title: string;
  children?: ReactNode;
}

export function EmptyState({ title, children }: EmptyStateProps) {
  return (
    <div className="state state--empty" role="status">
      <div className="state__body">
        <p className="state__title">{title}</p>
        {children && <div className="state__text">{children}</div>}
      </div>
    </div>
  );
}

/** Live-region props, omitted when a sibling <LiveStatus> already announces loading. */
function liveProps(isAnnounced: boolean) {
  return isAnnounced ? ({ role: 'status', 'aria-live': 'polite' } as const) : {};
}

interface SkeletonProps {
  lines?: number;
  label?: string;
  isAnnounced?: boolean;
}

/** Shimmering placeholder lines with an accessible "loading" label. */
export function Skeleton({ lines = 3, label = 'Loading…', isAnnounced = true }: SkeletonProps) {
  return (
    <div className="skeleton" {...liveProps(isAnnounced)}>
      <span className="sr-only">{label}</span>
      {Array.from({ length: lines }, (_, index) => (
        <span key={index} className="skeleton__line" aria-hidden="true" />
      ))}
    </div>
  );
}

interface TableSkeletonProps {
  rows?: number;
  columns?: number;
  label?: string;
  isAnnounced?: boolean;
}

export function TableSkeleton({ rows = 8, columns = 5, label = 'Loading table…', isAnnounced = true }: TableSkeletonProps) {
  return (
    <div className="skeleton skeleton--table" {...liveProps(isAnnounced)}>
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, row) => (
        <div key={row} className="skeleton__row" aria-hidden="true">
          {Array.from({ length: columns }, (_, column) => (
            <span key={column} className="skeleton__cell" />
          ))}
        </div>
      ))}
    </div>
  );
}
