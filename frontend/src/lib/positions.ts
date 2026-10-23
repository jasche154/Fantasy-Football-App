import type { Position } from '../api/types';

export const POSITIONS: readonly Position[] = ['QB', 'RB', 'WR', 'TE'];

export type PositionFilterValue = Position | 'ALL';

export const POSITION_FILTERS: readonly PositionFilterValue[] = ['ALL', ...POSITIONS];

export function isPosition(value: unknown): value is Position {
  return typeof value === 'string' && (POSITIONS as readonly string[]).includes(value);
}

/** Converts the UI filter value into the optional `position` API param. */
export function filterToPosition(filter: PositionFilterValue): Position | undefined {
  return filter === 'ALL' ? undefined : filter;
}

export function positionFilterLabel(filter: PositionFilterValue): string {
  return filter === 'ALL' ? 'All' : filter;
}
