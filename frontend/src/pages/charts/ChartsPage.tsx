import { useState } from 'react';
import { getLeaderboardMetrics } from '../../api/data';
import type { MetricCategory, PlayerSummary } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { PageHeader } from '../../components/Card';
import { Icon } from '../../components/Icon';
import { PlayerSearch } from '../../components/PlayerSearch';
import { PositionFilter } from '../../components/PositionFilter';
import { SelectField } from '../../components/SelectField';
import { Skeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { useDocumentTitle } from '../../hooks/useDocumentTitle';
import { filterToPosition, type PositionFilterValue } from '../../lib/positions';
import {
  chooseChart, MAX_TREND_PLAYERS, PRESETS, WEEKLY_STATS,
  type ChartPreset, type MetricId, type WeeklyStatKey,
} from './autoChart';
import { ChartView } from './ChartView';

const NONE = '';

function metricOptions(catalog: readonly MetricCategory[], emptyLabel: string) {
  return [
    { value: NONE, label: emptyLabel },
    ...catalog.flatMap((category) => category.metrics.map((metric) => ({
      value: `${category.key}:${metric.key}`, label: `${category.label.split(' (')[0]} · ${metric.label}`,
    }))),
  ];
}

function Presets({ onPick }: { onPick: (preset: ChartPreset) => void }) {
  return (
    <ul className="chart-presets" aria-label="Quick charts">
      {PRESETS.map((preset) => (
        <li key={preset.name}>
          <button type="button" className="chart-preset" onClick={() => onPick(preset)}>
            <span className="chart-preset__name">{preset.name}</span>
            <span className="chart-preset__blurb">{preset.blurb}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function Studio({ catalog }: { catalog: readonly MetricCategory[] }) {
  const [players, setPlayers] = useState<readonly PlayerSummary[]>([]);
  const [weeklyStat, setWeeklyStat] = useState<WeeklyStatKey>('fantasy_points');
  const [metrics, setMetrics] = useState<readonly MetricId[]>(PRESETS[0].metrics);
  const [filter, setFilter] = useState<PositionFilterValue>(PRESETS[0].position ?? 'ALL');
  const selection = { players, weeklyStat, metrics, position: filterToPosition(filter) ?? null };
  const plan = chooseChart(selection);

  const setMetric = (slot: 0 | 1, value: string) => {
    const next = [...metrics];
    if (value === NONE) next.splice(slot, slot === 0 ? 2 : 1);   // clearing metric 1 clears both
    else next[slot] = value as MetricId;
    setMetrics(next.filter(Boolean));
  };
  const applyPreset = (preset: ChartPreset) => {
    setMetrics(preset.metrics);
    setFilter(preset.position ?? 'ALL');
  };

  return (
    <div className="studio">
      <aside className="studio__controls" aria-label="Chart builder">
        <h2 className="studio__heading">Quick charts</h2>
        <Presets onPick={applyPreset} />
        <h2 className="studio__heading">Build your own</h2>
        <PositionFilter value={filter} onChange={setFilter} />
        <SelectField label="Season metric" value={metrics[0] ?? NONE} options={metricOptions(catalog, 'None (weekly trend)')}
          onChange={(value) => setMetric(0, value)} />
        <SelectField label="Compare with" value={metrics[1] ?? NONE} options={metricOptions(catalog, 'Nothing (ranked bars)')}
          onChange={(value) => setMetric(1, value)} disabled={metrics.length === 0} />
        <PlayerSearch label="Add a player to track or highlight" excludeIds={players.map((p) => p.id)}
          onSelect={(player) => setPlayers((current) => [...current, player].slice(0, MAX_TREND_PLAYERS))} />
        {players.length > 0 && (
          <ul className="chip-list" aria-label="Chart players">
            {players.map((player) => (
              <li key={player.id} className="chip">
                {player.name}
                <button type="button" className="chip__remove" aria-label={`Remove ${player.name} from the chart`}
                  onClick={() => setPlayers((current) => current.filter((p) => p.id !== player.id))}>
                  <Icon name="close" size={12} />
                </button>
              </li>
            ))}
          </ul>
        )}
        <SelectField label="Weekly stat (trend)" value={weeklyStat} options={WEEKLY_STATS.map((s) => ({ value: s.key, label: s.label }))}
          onChange={(value) => setWeeklyStat(value as WeeklyStatKey)} disabled={metrics.length > 0} />
        <p className="field__hint">
          The chart type is picked for you: two metrics make a scatter, one makes ranked bars, and players with no
          metric make a week-by-week trend. Added players are highlighted on scatters and bars.
        </p>
      </aside>
      <div className="studio__canvas">
        {plan ? <ChartView plan={plan} selection={selection} catalog={catalog} /> : (
          <p className="studio__empty">Pick a quick chart, a season metric, or add a player to start.</p>
        )}
      </div>
    </div>
  );
}

export function ChartsPage() {
  useDocumentTitle('Chart Studio');
  const catalog = useAsync((signal) => getLeaderboardMetrics(signal), []);
  return (
    <>
      <PageHeader title="Chart Studio" subtitle="Charts built automatically from this season's stats. Download any of them as SVG." />
      <AsyncView state={catalog} loading={<Skeleton lines={8} label="Loading metrics…" />} errorTitle="Couldn't load the metric catalog">
        {(data) => <Studio catalog={data.categories} />}
      </AsyncView>
    </>
  );
}
