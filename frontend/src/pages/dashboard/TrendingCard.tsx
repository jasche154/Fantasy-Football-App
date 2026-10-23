import { ApiError } from '../../api/client';
import { getTrending } from '../../api/data';
import type { TrendingRow } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { Card } from '../../components/Card';
import { PlayerLink } from '../../components/PlayerLink';
import { EmptyState, ErrorState, Skeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { formatInteger } from '../../lib/format';

const TRENDING_LIMIT = 10;

export const SLEEPER_DOWN_MESSAGE =
  'Sleeper’s trending feed is unreachable right now. Everything else still works — try again in a minute.';

function TrendingItem({ row, maxCount }: { row: TrendingRow; maxCount: number }) {
  const width = maxCount > 0 ? Math.max(4, Math.round((row.count / maxCount) * 100)) : 0;
  return (
    <li className="trending__row">
      <div className="trending__name">
        {row.player ? (
          <PlayerLink player={row.player} />
        ) : (
          <span className="muted">Unmatched Sleeper player #{row.sleeper_id}</span>
        )}
      </div>
      <div className="trending__count">
        <span className="trending__bar" style={{ inlineSize: `${width}%` }} aria-hidden="true" />
        <span className="num">{formatInteger(row.count)}</span>
        <span className="sr-only"> adds</span>
      </div>
    </li>
  );
}

export function TrendingCard() {
  const trending = useAsync((signal) => getTrending('add', TRENDING_LIMIT, signal), []);
  return (
    <Card title="Trending adds" eyebrow="Sleeper · most-added players" className="card--trending">
      <AsyncView
        state={trending}
        loading={<Skeleton lines={6} label="Loading trending adds…" />}
        isEmpty={(data) => data.results.length === 0}
        empty={<EmptyState title="No trending adds reported yet." />}
        renderError={(error, retry) => (
          <ErrorState
            error={error}
            title="Trending data unavailable"
            message={error instanceof ApiError && error.status === 502 ? SLEEPER_DOWN_MESSAGE : undefined}
            onRetry={retry}
          />
        )}
      >
        {(data) => {
          const maxCount = Math.max(0, ...data.results.map((row) => row.count));
          return (
            <ol className="trending">
              {data.results.map((row) => (
                <TrendingItem key={row.sleeper_id} row={row} maxCount={maxCount} />
              ))}
            </ol>
          );
        }}
      </AsyncView>
    </Card>
  );
}
