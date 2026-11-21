/**
 * Which stat columns the Players table shows: a set per stats span (Season, Last 4 avg, Week N) plus box-score
 * columns that fit the position tab. Pure, so the choice of columns and sort keys is tested on its own.
 */
import type { SortDirection, SortState } from '../../components/SortableHeader';
import type { ReplayBoxKey, ReplayBoxLine, ReplayPlayerSort, ReplayPosition, ReplayStatsSpan, ReplayWireRow } from '../../api/types';
import { EMPTY_VALUE, formatNumber, formatPercentValue } from '../../lib/format';
import { formatPoints, type ReplayPositionTab } from './shared';

export interface StatColumn {
  id: string;
  label: string;
  /** Full meaning, for the header's tooltip. */
  title: string;
  /** null: the column can't be sorted on the server. */
  sort: ReplayPlayerSort | null;
  text: (row: ReplayWireRow) => string;
}

export interface BoxColumn {
  key: ReplayBoxKey;
  label: string;
  title: string;
}

const BOX: Record<ReplayBoxKey, BoxColumn> = {
  pass_yds: { key: 'pass_yds', label: 'PaYd', title: 'Passing yards' },
  pass_td: { key: 'pass_td', label: 'PaTD', title: 'Passing touchdowns' },
  pass_int: { key: 'pass_int', label: 'INT', title: 'Interceptions thrown' },
  carries: { key: 'carries', label: 'Car', title: 'Carries' },
  rush_yds: { key: 'rush_yds', label: 'RuYd', title: 'Rushing yards' },
  rush_td: { key: 'rush_td', label: 'RuTD', title: 'Rushing touchdowns' },
  targets: { key: 'targets', label: 'Tgt', title: 'Targets' },
  rec: { key: 'rec', label: 'Rec', title: 'Catches' },
  rec_yds: { key: 'rec_yds', label: 'ReYd', title: 'Receiving yards' },
  rec_td: { key: 'rec_td', label: 'ReTD', title: 'Receiving touchdowns' },
};

const RECEIVER: readonly ReplayBoxKey[] = ['targets', 'rec', 'rec_yds', 'rec_td'];
const MIXED: readonly ReplayBoxKey[] = ['carries', 'rush_yds', 'targets', 'rec', 'rec_yds'];
const BOX_KEYS: Record<ReplayPositionTab, readonly ReplayBoxKey[]> = {
  ALL: MIXED,
  FLEX: MIXED,
  QB: ['pass_yds', 'pass_td', 'pass_int', 'rush_yds'],
  RB: MIXED,
  WR: RECEIVER,
  TE: RECEIVER,
  K: [],
  DST: [],
};

/** The box-score columns for a position tab or one player's position (none for K/DST). */
export function boxColumns(position: ReplayPositionTab | ReplayPosition): BoxColumn[] {
  return BOX_KEYS[position].map((key) => BOX[key]);
}

/** A box number: averages keep one decimal, totals and single games are whole. */
export function boxText(box: ReplayBoxLine | null, key: ReplayBoxKey, isAverage = false): string {
  if (!box) return EMPTY_VALUE;
  return formatNumber(box[key], isAverage ? 1 : 0);
}

function spanColumns(span: ReplayStatsSpan, week: number | null): StatColumn[] {
  if (span === 'last4') {
    return [
      { id: 'pts', label: 'Pts/G', title: 'Points a game, last 4 games played', sort: 'line_points', text: (row) => formatPoints(row.line.points) },
      {
        id: 'snap', label: 'Snap %', title: 'Share of offensive snaps, last 3 games', sort: null,
        text: (row) => formatPercentValue(row.usage?.snap_pct ?? null, 0),
      },
    ];
  }
  if (span === 'week') {
    return [
      { id: 'proj', label: 'Proj', title: `Week ${week} pre-game projection`, sort: 'line_projection', text: (row) => formatPoints(row.line.projection) },
      // A week not played yet (or one he sat out) shows a dash, never a zero.
      { id: 'pts', label: 'Pts', title: `Points in week ${week}`, sort: 'line_points', text: (row) => formatPoints(row.line.points) },
    ];
  }
  return [
    { id: 'pts', label: 'Pts', title: 'Points this season', sort: 'line_points', text: (row) => formatPoints(row.line.points) },
    { id: 'ppg', label: 'PPG', title: 'Points per game played', sort: 'ppg', text: (row) => (row.games > 0 ? formatPoints(row.ppg) : EMPTY_VALUE) },
    { id: 'gp', label: 'GP', title: 'Games played', sort: null, text: (row) => String(row.line.games) },
    { id: 'ros', label: 'ROS', title: 'Expected points per game for the rest of the season', sort: 'ros_ppg', text: (row) => formatPoints(row.ros_ppg) },
  ];
}

const SPAN_NAME: Record<ReplayStatsSpan, string> = { season: 'this season', last4: 'a game, last 4 games', week: 'that week' };

/** The stat columns between Opp and Value for a span and position tab. */
export function statColumns(span: ReplayStatsSpan, week: number | null, tab: ReplayPositionTab): StatColumn[] {
  const box = boxColumns(tab).map(({ key, label, title }): StatColumn => ({
    id: key, label, title: `${title}, ${SPAN_NAME[span]}`, sort: key,
    text: (row) => boxText(row.line.box, key, span === 'last4'),
  }));
  return [...spanColumns(span, week), ...box];
}

export const DEFAULT_SORT: SortState<ReplayPlayerSort> = { key: 'value', direction: 'desc' };

/** The server's default: names and ADP read best low to high, everything else high to low. */
export function sortDirection(key: ReplayPlayerSort): SortDirection {
  return key === 'name' || key === 'adp' ? 'asc' : 'desc';
}

/** The sort in force: the chosen one while its column is on screen, else the default (Value). */
export function visibleSort(sort: SortState<ReplayPlayerSort>, columns: readonly StatColumn[]): SortState<ReplayPlayerSort> {
  if (sort.key === 'name' || sort.key === 'value') return sort;
  return columns.some((column) => column.sort === sort.key) ? sort : DEFAULT_SORT;
}
