import type { GameLogRow } from '../../api/types';
import { formatInteger, formatNumber, formatPercentValue } from '../../lib/format';

type StatKey = keyof Pick<
  GameLogRow,
  | 'passing_yards'
  | 'passing_tds'
  | 'interceptions'
  | 'carries'
  | 'rushing_yards'
  | 'rushing_tds'
  | 'targets'
  | 'receptions'
  | 'receiving_yards'
  | 'receiving_tds'
>;

interface StatGroup {
  key: 'passing' | 'rushing' | 'receiving';
  label: string;
  columns: ReadonlyArray<{ key: StatKey; label: string; title: string }>;
}

export const STAT_GROUPS: readonly StatGroup[] = [
  {
    key: 'passing',
    label: 'Passing',
    columns: [
      { key: 'passing_yards', label: 'Yds', title: 'Passing yards' },
      { key: 'passing_tds', label: 'TD', title: 'Passing touchdowns' },
      { key: 'interceptions', label: 'INT', title: 'Interceptions' },
    ],
  },
  {
    key: 'rushing',
    label: 'Rushing',
    columns: [
      { key: 'carries', label: 'Car', title: 'Carries' },
      { key: 'rushing_yards', label: 'Yds', title: 'Rushing yards' },
      { key: 'rushing_tds', label: 'TD', title: 'Rushing touchdowns' },
    ],
  },
  {
    key: 'receiving',
    label: 'Receiving',
    columns: [
      { key: 'targets', label: 'Tgt', title: 'Targets' },
      { key: 'receptions', label: 'Rec', title: 'Receptions' },
      { key: 'receiving_yards', label: 'Yds', title: 'Receiving yards' },
      { key: 'receiving_tds', label: 'TD', title: 'Receiving touchdowns' },
    ],
  },
];

/** Hides stat groups that are all zeros (e.g. passing columns for a receiver). */
export function visibleStatGroups(rows: readonly GameLogRow[]): StatGroup[] {
  return STAT_GROUPS.filter((group) =>
    rows.some((row) => group.columns.some((column) => row[column.key] !== 0)),
  );
}

function weekLabel(row: GameLogRow): string {
  return row.season_type && row.season_type !== 'REG' ? `${row.week} (${row.season_type})` : String(row.week);
}

export function GameLogTable({ rows }: { rows: readonly GameLogRow[] }) {
  const groups = visibleStatGroups(rows);
  return (
    <div className="table-wrap table-wrap--tall">
      <table className="data-table data-table--compact">
        <caption className="sr-only">Game log, one row per week. FP is actual fantasy points; xFP is expected fantasy points.</caption>
        <thead>
          <tr>
            <th scope="col" className="th--end">Wk</th>
            <th scope="col" className="th--start">Opp</th>
            <th scope="col" className="th--end" title="Actual fantasy points">FP{' '}<span className="th-tag">Actual</span></th>
            <th scope="col" className="th--end" title="Expected fantasy points">xFP{' '}<span className="th-tag">Expected</span></th>
            {groups.flatMap((group) =>
              group.columns.map((column) => (
                <th key={column.key} scope="col" className="th--end" title={column.title}>
                  {column.label}{' '}
                  <span className="th-tag">{group.label}</span>
                </th>
              )),
            )}
            <th scope="col" className="th--end" title="Share of offensive snaps">Snap%</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${row.season_type ?? 'REG'}-${row.week}`}>
              <td className="num">{weekLabel(row)}</td>
              <td>{row.opponent ?? '—'}</td>
              <td className="num num--strong">{formatNumber(row.fantasy_points)}</td>
              <td className="num">{formatNumber(row.expected_points)}</td>
              {groups.flatMap((group) =>
                group.columns.map((column) => (
                  <td key={column.key} className="num">{formatInteger(row[column.key])}</td>
                )),
              )}
              <td className="num">{formatPercentValue(row.snap_pct, 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
