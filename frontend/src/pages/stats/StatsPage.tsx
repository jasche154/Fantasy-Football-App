import { useState } from 'react';
import { getLeaderboard, getLeaderboardMetrics } from '../../api/data';
import type { LeaderboardResponse, MetricCategory, MetricDef } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { Card, PageHeader } from '../../components/Card';
import { countLabel, LiveStatus } from '../../components/LiveStatus';
import { EmptyState, Skeleton, TableSkeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { useDebouncedValue } from '../../hooks/useDebouncedValue';
import { useDocumentTitle } from '../../hooks/useDocumentTitle';
import { formatInteger } from '../../lib/format';
import { filterToPosition } from '../../lib/positions';
import { useMeta } from '../../state/MetaContext';
import { LeaderboardControls } from './LeaderboardControls';
import {
  INITIAL_SELECTION,
  parseMinVolume,
  resolveMetric,
  withCategory,
  withMetric,
  type LeaderboardSelection,
  type ResolvedMetric,
} from './leaderboardSelection';
import { LeaderboardTable } from './LeaderboardTable';

const MIN_VOLUME_DEBOUNCE_MS = 400;

interface LeaderboardResultsProps {
  categories: readonly MetricCategory[];
  selection: LeaderboardSelection;
  resolved: ResolvedMetric;
}

/** Formats rows by the metric that produced them, not the one being loaded next. */
function metricFor(categories: readonly MetricCategory[], data: LeaderboardResponse, fallback: MetricDef): MetricDef {
  const category = categories.find((item) => item.key === data.category);
  return category?.metrics.find((item) => item.key === data.metric) ?? fallback;
}

function LeaderboardResults({ categories, selection, resolved }: LeaderboardResultsProps) {
  const debouncedVolume = useDebouncedValue(selection.minVolume, MIN_VOLUME_DEBOUNCE_MS);
  const parsedVolume = parseMinVolume(debouncedVolume);
  const minVolume = parsedVolume.ok ? parsedVolume.value : undefined;
  const position = filterToPosition(selection.position);
  const { category, metric } = resolved;
  const board = useAsync(
    (signal) =>
      getLeaderboard(
        { category: category.key, metric: metric.key, season: selection.season, min_volume: minVolume, limit: selection.limit, ascending: selection.ascending, position },
        signal,
      ),
    [category.key, metric.key, selection.season, minVolume, selection.limit, selection.ascending, position],
  );
  return (
    <>
      <LiveStatus state={board} describe={(data) => countLabel(data.results.length)} loadingText="Loading leaderboard…" />
      {board.data && (
        <p className="meta-line">
          {board.data.season} season · minimum {formatInteger(board.data.min_volume)}{' '}
          {metricFor(categories, board.data, metric).volume_label.toLowerCase()}
        </p>
      )}
      <AsyncView
        state={board}
        loading={<TableSkeleton rows={10} columns={4} label="Loading leaderboard…" isAnnounced={false} />}
        isEmpty={(data) => data.results.length === 0}
        empty={<EmptyState title="No players meet these filters.">Lower the minimum volume or pick another season.</EmptyState>}
      >
        {(data) => <LeaderboardTable data={data} metric={metricFor(categories, data, metric)} />}
      </AsyncView>
    </>
  );
}

function LeaderboardExplorer({ categories }: { categories: readonly MetricCategory[] }) {
  const meta = useMeta();
  const [selection, setSelection] = useState<LeaderboardSelection>(() => withCategory(INITIAL_SELECTION, categories, ''));
  const resolved = resolveMetric(categories, selection);
  if (!resolved) return <EmptyState title="No leaderboard metrics are available." />;
  const displaySeason = selection.season ?? meta.data?.current_season;
  const knownSeasons = meta.data ? [...meta.data.seasons].sort((a, b) => b - a) : [];
  const seasons =
    displaySeason !== undefined && !knownSeasons.includes(displaySeason) ? [displaySeason, ...knownSeasons] : knownSeasons;
  const update = (patch: Partial<LeaderboardSelection>) => setSelection((current) => ({ ...current, ...patch }));
  return (
    <>
      <LeaderboardControls
        categories={categories}
        resolved={resolved}
        selection={selection}
        seasons={seasons}
        displaySeason={displaySeason}
        onCategory={(key) => setSelection((current) => withCategory(current, categories, key))}
        onMetric={(key) => setSelection((current) => withMetric(current, categories, key))}
        onChange={update}
      />
      <Card title={resolved.metric.label} eyebrow={`${resolved.category.label} · ${resolved.metric.higher_is_better ? 'higher is better' : 'lower is better'}`}>
        <p className="metric-description">{resolved.metric.description}</p>
        <LeaderboardResults categories={categories} selection={selection} resolved={resolved} />
      </Card>
    </>
  );
}

export function StatsPage() {
  useDocumentTitle('Advanced Stats');
  const catalog = useAsync((signal) => getLeaderboardMetrics(signal), []);
  return (
    <>
      <PageHeader title="Advanced stats" subtitle="Volume-weighted leaderboards from Next Gen Stats and weekly usage data." />
      <AsyncView
        state={catalog}
        loading={<Skeleton lines={4} label="Loading metric catalog…" />}
        errorTitle="Couldn't load the metric catalog"
        isEmpty={(data) => data.categories.length === 0}
        empty={<EmptyState title="No leaderboard metrics are available." />}
      >
        {(data) => <LeaderboardExplorer categories={data.categories} />}
      </AsyncView>
    </>
  );
}
