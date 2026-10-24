import type { WeeklyPlayer, WeeklySourceKey } from '../../api/types';
import type { SortDirection, SortState } from '../../components/SortableHeader';

export type WeeklySortKey = 'rank' | 'implied' | 'proj' | WeeklySourceKey;

export const DEFAULT_WEEKLY_SORT: SortState<WeeklySortKey> = { key: 'rank', direction: 'asc' };

/** Rank reads best-first ascending; every points column reads best-first descending. */
export function weeklyDefaultDirection(key: WeeklySortKey): SortDirection {
  return key === 'rank' ? 'asc' : 'desc';
}

function valueFor(row: WeeklyPlayer, key: WeeklySortKey): number | null {
  if (key === 'rank') return row.rank;
  if (key === 'implied') return row.matchup.implied_total;
  if (key === 'proj') return row.projection;
  return row.sources[key]?.points ?? null;
}

/** A sorted copy; players a source doesn't cover always sink to the bottom. Ties keep rank order. */
export function sortWeekly(rows: readonly WeeklyPlayer[], sort: SortState<WeeklySortKey>): WeeklyPlayer[] {
  const sign = sort.direction === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const left = valueFor(a, sort.key);
    const right = valueFor(b, sort.key);
    if (left === null || right === null) {
      if (left !== right) return left === null ? 1 : -1;
      return a.rank - b.rank;
    }
    return (left - right) * sign || a.rank - b.rank;
  });
}
