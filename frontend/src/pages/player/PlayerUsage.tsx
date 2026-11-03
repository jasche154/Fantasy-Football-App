import { getPlayerUsage } from '../../api/data';
import type { PercentileStat, PlayerPercentiles, UsageChange, UsageChanges } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { Card } from '../../components/Card';
import { Skeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { formatNumber, formatPercentValue, formatSigned } from '../../lib/format';
import { useLeagueSettings } from '../../state/SettingsContext';

/** Percentiles at or above GOOD_AT read as good (green), at or below BAD_AT as bad (red). */
export const GOOD_AT = 67;
export const BAD_AT = 33;

export type PercentileTone = 'good' | 'bad' | 'neutral';

export function percentileTone(stat: Pick<PercentileStat, 'percentile' | 'direction'>): PercentileTone {
  if (stat.percentile === null || stat.direction === 'context') return 'neutral';
  if (stat.percentile >= GOOD_AT) return 'good';
  if (stat.percentile <= BAD_AT) return 'bad';
  return 'neutral';
}

export function formatStatValue(value: number | null, unit: string): string {
  return unit === '%' ? formatPercentValue(value) : formatNumber(value, 1);
}

export function formatChange(change: Pick<UsageChange, 'delta' | 'unit'>): string {
  return change.unit === '%' ? `${formatSigned(change.delta)} pts` : `${formatSigned(change.delta)}/g`;
}

function PercentileRow({ stat }: { stat: PercentileStat }) {
  const tone = percentileTone(stat);
  const sampleNote = `${formatNumber(stat.sample, 0)} of ${formatNumber(stat.min_sample, 0)} ${stat.sample_unit} needed`;
  return (
    <div className="percentile-row">
      <dt>
        {stat.label}
        {stat.direction === 'lower' && <span className="sr-only"> (lower is better)</span>}
        {stat.direction === 'context' && <span className="th-tag" title="Context only: neither good nor bad">Context</span>}
      </dt>
      <dd className="num">{formatStatValue(stat.value, stat.unit)}</dd>
      <dd>
        {stat.percentile === null ? (
          <span className="muted">
            {stat.small_sample ? (
              <>Small sample <span className="note">({sampleNote})</span></>
            ) : 'No pool'}
          </span>
        ) : (
          <span className="percentile-bar" aria-hidden="true">
            <span className={`percentile-bar__fill percentile-bar__fill--${tone}`} style={{ width: `${stat.percentile}%` }} />
          </span>
        )}
      </dd>
      <dd className={`percentile-row__pct num${tone === 'good' ? ' num--pos' : tone === 'bad' ? ' num--neg' : ''}`}>
        {stat.percentile === null ? '—' : (
          <>
            {stat.percentile}
            <span className="sr-only">th percentile</span>
          </>
        )}
      </dd>
    </div>
  );
}

function PercentilesView({ percentiles }: { percentiles: PlayerPercentiles }) {
  if (percentiles.stats.length === 0) return <p className="muted">No games this season to compare.</p>;
  return (
    <>
      <dl className="percentile-list">
        {percentiles.stats.map((stat) => <PercentileRow key={stat.key} stat={stat} />)}
      </dl>
      <p className="note">
        Percentile among {percentiles.pool_players} {percentiles.position}s with {percentiles.pool_min_games}+ games
        (higher is better; flipped for INT rate and fumbles). Each stat needs its own minimum sample.
      </p>
    </>
  );
}

function ChangesView({ changes }: { changes: UsageChanges }) {
  if (changes.metrics.length === 0) return <p className="muted">{changes.note ?? 'No usage data to compare yet.'}</p>;
  const weeks = changes.recent_weeks;
  return (
    <>
      <div className="table-wrap">
        <table className="data-table data-table--compact">
          <caption className="sr-only">Usage over the last {changes.recent_games} games against the games before them</caption>
          <thead>
            <tr>
              <th scope="col" className="th--start">Usage</th>
              <th scope="col" className="th--end">Before ({changes.baseline_games} g)</th>
              <th scope="col" className="th--end">Last {changes.recent_games}</th>
              <th scope="col" className="th--end">Change</th>
            </tr>
          </thead>
          <tbody>
            {changes.metrics.map((metric) => (
              <tr key={metric.key}>
                <th scope="row" className="th--start">{metric.label}</th>
                <td className="num">{formatStatValue(metric.before, metric.unit)}</td>
                <td className="num">{formatStatValue(metric.recent, metric.unit)}</td>
                <td className="num">
                  {formatChange(metric)}
                  {metric.flag && (
                    <>
                      {' '}
                      <span className={`change-flag change-flag--${metric.flag}`} title={`Flagged at ±${metric.flag_at}`}>{metric.flag === 'up' ? 'Big rise' : 'Big drop'}</span>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="note">
        Weeks {weeks.join(', ')} against the earlier games this season. Shares change in percentage points; big moves
        are flagged.
      </p>
    </>
  );
}

export function PlayerUsage({ playerId, season }: { playerId: number; season: number }) {
  const { settings } = useLeagueSettings();
  const usage = useAsync((signal) => getPlayerUsage(playerId, settings, season, signal), [playerId, season, settings]);
  return (
    <div className="advanced-grid">
      <Card title="Position percentiles" eyebrow={`${season} · vs same position`}>
        <AsyncView state={usage} loading={<Skeleton lines={5} label="Loading percentiles…" />} errorTitle="Couldn't load percentiles">
          {(data) => data.percentiles ? <PercentilesView percentiles={data.percentiles} /> : <p className="muted">No percentiles for this player.</p>}
        </AsyncView>
      </Card>
      <Card title="What changed" eyebrow={usage.data?.changes ? `Last ${usage.data.changes.recent_games} games vs before` : 'Recent games vs before'}>
        <AsyncView state={usage} loading={<Skeleton lines={4} label="Loading recent usage…" />} errorTitle="Couldn't load recent usage">
          {(data) => data.changes ? <ChangesView changes={data.changes} /> : <p className="muted">No usage data for this player.</p>}
        </AsyncView>
      </Card>
    </div>
  );
}
