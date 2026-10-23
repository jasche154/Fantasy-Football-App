import type { ReactNode } from 'react';

export type SortDirection = 'asc' | 'desc';

export interface SortState<K extends string> {
  key: K;
  direction: SortDirection;
}

interface SortableHeaderProps<K extends string> {
  columnKey: K;
  sort: SortState<K>;
  onSort: (key: K) => void;
  children: ReactNode;
  /** Provenance tag under the label, e.g. "Actual", "Expected", "Projection (model)". */
  tag?: string;
  align?: 'start' | 'end';
  title?: string;
}

function ariaSortFor(isActive: boolean, direction: SortDirection): 'ascending' | 'descending' | 'none' {
  if (!isActive) return 'none';
  return direction === 'asc' ? 'ascending' : 'descending';
}

export function SortableHeader<K extends string>({
  columnKey,
  sort,
  onSort,
  children,
  tag,
  align = 'end',
  title,
}: SortableHeaderProps<K>) {
  const isActive = sort.key === columnKey;
  const arrow = isActive ? (sort.direction === 'asc' ? '▲' : '▼') : '↕';
  return (
    <th
      scope="col"
      aria-sort={ariaSortFor(isActive, sort.direction)}
      className={`th-sortable th--${align}${isActive ? ' is-sorted' : ''}`}
      title={title}
    >
      <button type="button" className="th-sortable__button" onClick={() => onSort(columnKey)}>
        <span className="th-sortable__label">
          <span>{children}</span>
          {tag && ' '}
          {tag && <span className="th-tag">{tag}</span>}
        </span>
        <span className="th-sortable__arrow" aria-hidden="true">
          {arrow}
        </span>
      </button>
    </th>
  );
}

/** Next sort state after clicking `key`: toggles direction, or starts with the column default. */
export function nextSort<K extends string>(
  current: SortState<K>,
  key: K,
  defaultDirection: (key: K) => SortDirection,
): SortState<K> {
  if (current.key === key) {
    return { key, direction: current.direction === 'asc' ? 'desc' : 'asc' };
  }
  return { key, direction: defaultDirection(key) };
}
