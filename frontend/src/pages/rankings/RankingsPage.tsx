import { useState } from 'react';
import { getRankings } from '../../api/data';
import type { RankingsResponse } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { PageHeader } from '../../components/Card';
import { PositionFilter } from '../../components/PositionFilter';
import { countLabel, LiveStatus } from '../../components/LiveStatus';
import { EmptyState, TableSkeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { useDocumentTitle } from '../../hooks/useDocumentTitle';
import { filterToPosition, positionFilterLabel, type PositionFilterValue } from '../../lib/positions';
import { describeSettings } from '../../state/leagueSettings';
import { useLeagueSettings } from '../../state/SettingsContext';
import { RankingsTable } from './RankingsTable';

const RANKINGS_LIMIT = 100;

function rankingsContext(data: RankingsResponse): string {
  const through = data.through_week === null ? 'preseason' : `through week ${data.through_week}`;
  return `${data.season} · ${through} · ${data.weeks_remaining} weeks remaining`;
}

function Legend() {
  return (
    <p className="legend">
      <span><strong>Actual</strong> = box-score fantasy points.</span>
      <span><strong>Expected</strong> = points the player’s opportunity predicts (nflverse).</span>
      <span><strong>Projection (model)</strong> = our rest-of-season estimate; value = points above replacement.</span>
      <span><strong>ECR</strong> = FantasyPros expert consensus.</span>
    </p>
  );
}

export function RankingsPage() {
  useDocumentTitle('Rankings');
  const { settings } = useLeagueSettings();
  const [filter, setFilter] = useState<PositionFilterValue>('ALL');
  const position = filterToPosition(filter);
  const rankings = useAsync(
    (signal) => getRankings(settings, { position, limit: RANKINGS_LIMIT }, signal),
    [position, settings],
  );
  const label = positionFilterLabel(filter);
  return (
    <>
      <PageHeader
        title="Rankings"
        subtitle={
          rankings.data ? `${rankingsContext(rankings.data)} · ${describeSettings(settings)}` : describeSettings(settings)
        }
        actions={<PositionFilter value={filter} onChange={setFilter} />}
      />
      <Legend />
      <section aria-label={`${label} rankings`}>
        <LiveStatus state={rankings} describe={(data) => countLabel(data.results.length)} loadingText="Loading rankings…" />
        <AsyncView
          state={rankings}
          loading={<TableSkeleton rows={12} columns={9} label="Loading rankings…" isAnnounced={false} />}
          isEmpty={(data) => data.results.length === 0}
          empty={<EmptyState title={`No ${label === 'All' ? '' : `${label} `}rankings yet.`}>Rankings appear once the season has stats.</EmptyState>}
        >
          {(data) => (
            <RankingsTable
              rows={data.results}
              caption={`${label} fantasy rankings, ${rankingsContext(data)}. Column headers sort the table.`}
            />
          )}
        </AsyncView>
      </section>
    </>
  );
}
