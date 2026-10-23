import type { RankedPlayer } from '../../api/types';
import type { SortDirection, SortState } from '../../components/SortableHeader';

export type RankingSortKey =
  | 'rank'
  | 'name'
  | 'pos_rank'
  | 'games'
  | 'ppg'
  | 'expected_ppg'
  | 'ros_ppg'
  | 'schedule_factor'
  | 'trade_value'
  | 'ecr';

export const DEFAULT_RANKING_SORT: SortState<RankingSortKey> = { key: 'rank', direction: 'asc' };

const ASCENDING_FIRST: ReadonlySet<RankingSortKey> = new Set(['rank', 'name', 'pos_rank', 'ecr']);

/** Ranks and names read best ascending; counting stats read best descending. */
export function defaultRankingDirection(key: RankingSortKey): SortDirection {
  return ASCENDING_FIRST.has(key) ? 'asc' : 'desc';
}

type Comparable = number | string | null;

/** "RB12" -> ["RB", 12] so RB2 sorts before RB10. */
function posRankParts(value: string): [string, number] {
  const match = /^([A-Za-z]+)(\d+)$/.exec(value.trim());
  return match ? [match[1].toUpperCase(), Number(match[2])] : [value, Number.POSITIVE_INFINITY];
}

function comparePosRank(a: string, b: string): number {
  const [posA, numA] = posRankParts(a);
  const [posB, numB] = posRankParts(b);
  return posA === posB ? numA - numB : posA.localeCompare(posB);
}

function valueFor(row: RankedPlayer, key: RankingSortKey): Comparable {
  switch (key) {
    case 'name':
      return row.player.name;
    case 'pos_rank':
      return row.pos_rank;
    default:
      return row[key];
  }
}

function compareValues(a: Comparable, b: Comparable, key: RankingSortKey): number {
  if (typeof a === 'string' && typeof b === 'string') {
    return key === 'pos_rank' ? comparePosRank(a, b) : a.localeCompare(b);
  }
  return Number(a) - Number(b);
}

/** Returns a new sorted array; missing values (null ECR / xPPG) always sink to the bottom. */
export function sortRankings(
  rows: readonly RankedPlayer[],
  sort: SortState<RankingSortKey>,
): RankedPlayer[] {
  const factor = sort.direction === 'asc' ? 1 : -1;
  return [...rows].sort((rowA, rowB) => {
    const a = valueFor(rowA, sort.key);
    const b = valueFor(rowB, sort.key);
    if (a === null && b === null) return rowA.rank - rowB.rank;
    if (a === null) return 1;
    if (b === null) return -1;
    const result = compareValues(a, b, sort.key) * factor;
    return result !== 0 ? result : rowA.rank - rowB.rank;
  });
}
