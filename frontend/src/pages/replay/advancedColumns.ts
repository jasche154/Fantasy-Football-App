/**
 * The Players table's Advanced view: usage and efficiency per position, from the StatLine's `adv`
 * (backend/services/replay_advanced.py). Every column sorts on the server as `adv_<key>`.
 */
import type { ReplayAdvKey, ReplayAdvLine, ReplayPlayerSort, ReplayPosition } from '../../api/types';
import { EMPTY_VALUE, formatNumber, formatPercentValue } from '../../lib/format';
import type { ReplayPositionTab } from './shared';

type AdvFormat = 'pct' | 'one' | 'two' | 'count';

export interface AdvColumn {
  key: ReplayAdvKey;
  label: string;
  title: string;
  format: AdvFormat;
}

const ADV: Record<ReplayAdvKey, AdvColumn> = {
  snap_pct: { key: 'snap_pct', label: 'Snap%', title: 'Share of his team’s offensive snaps', format: 'pct' },
  target_share: { key: 'target_share', label: 'Tgt%', title: 'Share of his team’s targets', format: 'pct' },
  targets_pg: { key: 'targets_pg', label: 'Tgt/G', title: 'Targets a game', format: 'one' },
  carries_pg: { key: 'carries_pg', label: 'Car/G', title: 'Carries a game', format: 'one' },
  touches_pg: { key: 'touches_pg', label: 'Tch/G', title: 'Touches (carries + catches) a game', format: 'one' },
  ypc: { key: 'ypc', label: 'YPC', title: 'Yards per carry', format: 'one' },
  ypr: { key: 'ypr', label: 'Y/Rec', title: 'Yards per catch', format: 'one' },
  ypt: { key: 'ypt', label: 'Y/Tgt', title: 'Receiving yards per target', format: 'one' },
  catch_rate: { key: 'catch_rate', label: 'Catch%', title: 'Catches per target', format: 'pct' },
  total_td: { key: 'total_td', label: 'TD', title: 'Touchdowns (passing, rushing and receiving)', format: 'count' },
  td_rate: { key: 'td_rate', label: 'TD%', title: 'Rushing and receiving touchdowns per touch', format: 'pct' },
  pass_ypg: { key: 'pass_ypg', label: 'PaYd/G', title: 'Passing yards a game', format: 'one' },
  td_int: { key: 'td_int', label: 'TD:INT', title: 'Passing touchdowns per interception', format: 'two' },
  cmp_pct: { key: 'cmp_pct', label: 'Cmp%', title: 'Completions per pass attempt', format: 'pct' },
};

const RUSHER: readonly ReplayAdvKey[] = ['snap_pct', 'touches_pg', 'carries_pg', 'ypc', 'target_share', 'targets_pg', 'catch_rate', 'ypr', 'td_rate', 'total_td'];
const RECEIVER: readonly ReplayAdvKey[] = ['snap_pct', 'target_share', 'targets_pg', 'catch_rate', 'ypt', 'ypr', 'td_rate', 'total_td'];
const ADV_KEYS: Record<ReplayPositionTab, readonly ReplayAdvKey[]> = {
  ALL: ['snap_pct', 'touches_pg', 'carries_pg', 'ypc', 'target_share', 'targets_pg', 'catch_rate', 'ypr', 'ypt', 'td_rate', 'total_td', 'pass_ypg', 'cmp_pct', 'td_int'],
  FLEX: [...RUSHER.slice(0, -2), 'ypt', ...RUSHER.slice(-2)],
  QB: ['snap_pct', 'pass_ypg', 'cmp_pct', 'td_int', 'carries_pg', 'ypc', 'total_td'],
  RB: RUSHER,
  WR: RECEIVER,
  TE: RECEIVER,
  K: [],
  DST: [],
};

/** The Advanced columns for a position tab (none for K/DST). */
export function advColumns(position: ReplayPositionTab | ReplayPosition): AdvColumn[] {
  return ADV_KEYS[position].map((key) => ADV[key]);
}

export function advSort(key: ReplayAdvKey): ReplayPlayerSort {
  return `adv_${key}`;
}

/** An advanced number: percents and rates with one decimal, TD:INT with two, TD whole unless averaged. */
export function advText(adv: ReplayAdvLine | null | undefined, column: AdvColumn, isAverage = false): string {
  const value = adv?.[column.key];
  if (value === null || value === undefined) return EMPTY_VALUE;
  if (column.format === 'pct') return formatPercentValue(value, 1);
  if (column.format === 'two') return formatNumber(value, 2);
  if (column.format === 'count') return formatNumber(value, isAverage ? 1 : 0);
  return formatNumber(value, 1);
}
