import { useMemo, useState } from 'react';
import type { RankedPlayer } from '../../api/types';
import { PlayerLink } from '../../components/PlayerLink';
import { nextSort, SortableHeader, type SortState } from '../../components/SortableHeader';
import { formatInteger, formatNumber } from '../../lib/format';
import { factorLabel, factorTone } from '../../lib/matchup';
import {
  DEFAULT_RANKING_SORT,
  defaultRankingDirection,
  sortRankings,
  type RankingSortKey,
} from './rankingsSort';

interface ColumnDef {
  key: RankingSortKey;
  label: string;
  tag?: string;
  title: string;
  align: 'start' | 'end';
}

export const RANKING_COLUMNS: readonly ColumnDef[] = [
  { key: 'rank', label: 'Rank', align: 'end', title: 'Overall rank by trade value' },
  { key: 'name', label: 'Player', align: 'start', title: 'Player name' },
  { key: 'pos_rank', label: 'Pos', align: 'start', title: 'Positional rank by trade value' },
  { key: 'games', label: 'GP', tag: 'Actual', align: 'end', title: 'Games played' },
  { key: 'ppg', label: 'PPG', tag: 'Actual', align: 'end', title: 'Actual fantasy points per game' },
  { key: 'expected_ppg', label: 'xPPG', tag: 'Expected', align: 'end', title: 'Expected fantasy points per game from opportunity (nflverse)' },
  { key: 'ros_ppg', label: 'ROS PPG', tag: 'Projection (blend)', align: 'end', title: 'Rest-of-season points per game: 45% model, 35% Yahoo ROS experts, 20% this week\'s ESPN/props, adjusted for injuries' },
  { key: 'schedule_factor', label: 'Sched', tag: 'Projection (model)', align: 'end', title: 'Remaining schedule vs average, opponent-adjusted (+ = easier); already included in ROS PPG' },
  { key: 'trade_value', label: 'Value', tag: 'Projection (model)', align: 'end', title: 'Rest-of-season points above replacement, our model' },
  { key: 'ecr', label: 'ECR', tag: 'Expert', align: 'end', title: 'FantasyPros expert consensus rank' },
];

function rosTitle(row: RankedPlayer): string {
  const s = row.ros_sources;
  if (!s) return 'Rest-of-season points per game';
  const parts = [`model ${formatNumber(s.model)}`];
  if (s.experts !== null) parts.push(`Yahoo ROS experts ${formatNumber(s.experts)} (rank ${formatNumber(s.expert_rank, 1)})`);
  if (s.weekly !== null) parts.push(`this week ${formatNumber(s.weekly)}`);
  if (s.availability < 1) parts.push(`expected to play ${Math.round(s.availability * 100)}% of remaining games`);
  return `Blend of ${parts.join(', ')}`;
}

function RankingRow({ row }: { row: RankedPlayer }) {
  return (
    <tr>
      <td className="num td--rank">{row.rank}</td>
      <td className="td--player">
        <PlayerLink player={row.player} />
      </td>
      <td className="td--pos">{row.pos_rank}</td>
      <td className="num">{formatInteger(row.games)}</td>
      <td className="num num--strong">{formatNumber(row.ppg)}</td>
      <td className="num">{formatNumber(row.expected_ppg)}</td>
      <td className="num" title={rosTitle(row)}>{formatNumber(row.ros_ppg)}
        {row.ros_sources?.expert_rank != null && (
          <span className="source-rank">experts {row.player.position}{formatNumber(row.ros_sources.expert_rank, 0)}</span>
        )}
      </td>
      <td className={`num sos-text sos-text--${factorTone(row.schedule_factor)}`}>{factorLabel(row.schedule_factor)}</td>
      <td className="num">
        <span className="value-pill">{formatNumber(row.trade_value, 0)}</span>
      </td>
      <td className="num">{row.ecr === null ? '—' : formatNumber(row.ecr, 1)}</td>
    </tr>
  );
}

interface RankingsTableProps {
  rows: readonly RankedPlayer[];
  caption: string;
}

export function RankingsTable({ rows, caption }: RankingsTableProps) {
  const [sort, setSort] = useState<SortState<RankingSortKey>>(DEFAULT_RANKING_SORT);
  const sorted = useMemo(() => sortRankings(rows, sort), [rows, sort]);
  const handleSort = (key: RankingSortKey) =>
    setSort((current) => nextSort(current, key, defaultRankingDirection));
  return (
    <div className="table-wrap table-wrap--tall">
      <table className="data-table">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {RANKING_COLUMNS.map((column) => (
              <SortableHeader
                key={column.key}
                columnKey={column.key}
                sort={sort}
                onSort={handleSort}
                tag={column.tag}
                align={column.align}
                title={column.title}
              >
                {column.label}
              </SortableHeader>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <RankingRow key={row.player.id} row={row} />
          ))}
        </tbody>
      </table>
    </div>
  );
}
