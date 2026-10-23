import type { LeaderboardResponse, MetricDef } from '../../api/types';
import { PlayerLink } from '../../components/PlayerLink';
import { formatInteger, formatMetric } from '../../lib/format';

interface LeaderboardTableProps {
  data: LeaderboardResponse;
  metric: MetricDef;
}

export function LeaderboardTable({ data, metric }: LeaderboardTableProps) {
  return (
    <div className="table-wrap table-wrap--tall">
      <table className="data-table">
        <caption className="sr-only">
          {data.label} leaders, {data.season}, minimum {formatInteger(data.min_volume)} {metric.volume_label.toLowerCase()}.
        </caption>
        <thead>
          <tr>
            <th scope="col" className="th--end">Rank</th>
            <th scope="col" className="th--start">Player</th>
            <th scope="col" className="th--end">
              <span className="th-label">{data.label}</span>{' '}
              <span className="th-tag">{metric.higher_is_better ? 'Higher is better' : 'Lower is better'}</span>
            </th>
            <th scope="col" className="th--end">{metric.volume_label}</th>
          </tr>
        </thead>
        <tbody>
          {data.results.map((row) => (
            <tr key={row.player.id}>
              <td className="num td--rank">{row.rank}</td>
              <td className="td--player"><PlayerLink player={row.player} /></td>
              <td className="num num--strong">{formatMetric(row.value, metric.format)}</td>
              <td className="num">{formatInteger(row.volume)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
