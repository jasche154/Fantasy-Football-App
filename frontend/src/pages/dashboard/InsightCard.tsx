import { getInsights, type InsightKind } from '../../api/data';
import { AsyncView } from '../../components/AsyncView';
import { Card } from '../../components/Card';
import { InsightRow } from '../../components/InsightRow';
import { EmptyState, Skeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { buildHash, paths } from '../../router/routes';
import { useLeagueSettings } from '../../state/SettingsContext';

const DASHBOARD_LIMIT = 5;

const COPY: Readonly<Record<InsightKind, { title: string; hint: string; empty: string }>> = {
  'buy-low': {
    title: 'Buy low',
    hint: 'Strong usage, unlucky results',
    empty: 'No buy-low candidates right now.',
  },
  'sell-high': {
    title: 'Sell high',
    hint: 'Production running ahead of usage',
    empty: 'No sell-high candidates right now.',
  },
};

export function InsightCard({ kind }: { kind: InsightKind }) {
  const { settings } = useLeagueSettings();
  const insights = useAsync(
    (signal) => getInsights(kind, settings, { limit: DASHBOARD_LIMIT }, signal),
    [kind, settings],
  );
  const copy = COPY[kind];
  const rowKind = kind === 'buy-low' ? 'buy' : 'sell';
  return (
    <Card
      title={copy.title}
      eyebrow={copy.hint}
      className={`card--${rowKind}`}
      actions={<a className="link-more" href={buildHash(paths.market)}>See all</a>}
    >
      <AsyncView
        state={insights}
        loading={<Skeleton lines={5} label={`Loading ${copy.title.toLowerCase()} candidates…`} />}
        isEmpty={(data) => data.results.length === 0}
        empty={<EmptyState title={copy.empty}>Signals need at least 3 games of data.</EmptyState>}
      >
        {(data) => (
          <ol className="insight-list">
            {data.results.slice(0, DASHBOARD_LIMIT).map((insight) => (
              <InsightRow key={insight.player.id} insight={insight} kind={rowKind} isCompact />
            ))}
          </ol>
        )}
      </AsyncView>
    </Card>
  );
}
