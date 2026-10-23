import { useState } from 'react';
import { getInsights, type InsightKind } from '../../api/data';
import type { LeagueSettings } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { Card, PageHeader } from '../../components/Card';
import { InsightRow } from '../../components/InsightRow';
import { PositionFilter } from '../../components/PositionFilter';
import { countLabel, LiveStatus } from '../../components/LiveStatus';
import { EmptyState, Skeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { useDocumentTitle } from '../../hooks/useDocumentTitle';
import { filterToPosition, positionFilterLabel, type PositionFilterValue } from '../../lib/positions';
import { describeSettings } from '../../state/leagueSettings';
import { useLeagueSettings } from '../../state/SettingsContext';
import { FlagsCard } from './FlagsCard';

const MARKET_LIMIT = 15;

interface InsightListProps {
  kind: InsightKind;
  filter: PositionFilterValue;
  settings: LeagueSettings;
}

const LIST_COPY: Readonly<Record<InsightKind, { title: string; eyebrow: string }>> = {
  'buy-low': { title: 'Buy low', eyebrow: 'Usage says better days are coming' },
  'sell-high': { title: 'Sell high', eyebrow: 'Scoring ahead of the underlying usage' },
};

function InsightList({ kind, filter, settings }: InsightListProps) {
  const position = filterToPosition(filter);
  const insights = useAsync(
    (signal) => getInsights(kind, settings, { position, limit: MARKET_LIMIT }, signal),
    [kind, position, settings],
  );
  const copy = LIST_COPY[kind];
  const rowKind = kind === 'buy-low' ? 'buy' : 'sell';
  return (
    <Card title={copy.title} eyebrow={copy.eyebrow} className={`card--${rowKind}`}>
      <LiveStatus state={insights} describe={(data) => `${copy.title}: ${countLabel(data.results.length)}`} />
      <AsyncView
        state={insights}
        loading={<Skeleton lines={8} label={`Loading ${copy.title.toLowerCase()} list…`} isAnnounced={false} />}
        isEmpty={(data) => data.results.length === 0}
        empty={
          <EmptyState title={`No ${copy.title.toLowerCase()} candidates at ${positionFilterLabel(filter)}.`}>
            Try another position — signals need at least 3 games of data.
          </EmptyState>
        }
      >
        {(data) => (
          <ol className="insight-list">
            {data.results.map((insight) => (
              <InsightRow key={insight.player.id} insight={insight} kind={rowKind} />
            ))}
          </ol>
        )}
      </AsyncView>
    </Card>
  );
}

export function MarketPage() {
  useDocumentTitle('Buy Low / Sell High');
  const { settings } = useLeagueSettings();
  const [filter, setFilter] = useState<PositionFilterValue>('ALL');
  return (
    <>
      <PageHeader
        title="Buy low / Sell high"
        subtitle={
          <>
            Actual points vs. expected points from opportunity (nflverse). {describeSettings(settings)}.
          </>
        }
        actions={<PositionFilter value={filter} onChange={setFilter} />}
      />
      <div className="market-grid">
        <InsightList kind="buy-low" filter={filter} settings={settings} />
        <InsightList kind="sell-high" filter={filter} settings={settings} />
      </div>
      <div className="market-grid market-grid--flags">
        <FlagsCard kind="breakout" position={filterToPosition(filter)} />
        <FlagsCard kind="regression" position={filterToPosition(filter)} />
      </div>
    </>
  );
}
