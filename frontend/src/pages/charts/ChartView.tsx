import { useRef, useState } from 'react';
import type { MetricCategory } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { BarChart, type BarDatum } from '../../components/charts/BarChart';
import { LineChart, type LineSeries } from '../../components/charts/LineChart';
import { ScatterChart, type ScatterDatum } from '../../components/charts/ScatterChart';
import { Icon } from '../../components/Icon';
import { EmptyState, Skeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { seriesClass } from '../../lib/chartScale';
import { useLeagueSettings } from '../../state/SettingsContext';
import { findMetric, WEEKLY_STATS, type ChartPlan, type ChartSelection } from './autoChart';
import { LEADERS_SHOWN, loadLeaders, loadScatter, loadTrend } from './chartData';
import { ScatterPointCard } from './ScatterPointCard';

type ChartResult =
  | { kind: 'trend'; series: LineSeries[] }
  | { kind: 'leaders'; bars: BarDatum[] }
  | { kind: 'scatter'; points: ScatterDatum[] };

/** Saves the rendered chart as a standalone SVG file. */
function downloadSvg(svg: SVGSVGElement | null, filename: string): void {
  if (!svg) return;
  const clone = svg.cloneNode(true) as SVGSVGElement;
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  const styles = getComputedStyle(document.documentElement);
  clone.setAttribute('style', `background:${styles.getPropertyValue('--color-surface-1')};font-family:${styles.getPropertyValue('--font-sans')}`);
  const url = URL.createObjectURL(new Blob([clone.outerHTML], { type: 'image/svg+xml' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = `${filename}.svg`;
  link.click();
  URL.revokeObjectURL(url);
}

function slug(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');
}

function SeriesLegend({ series }: { series: readonly LineSeries[] }) {
  return (
    <ul className="series-legend">
      {series.map((item, index) => (
        <li key={item.name}><span className={`series-swatch ${seriesClass(index)}`} aria-hidden="true" />{item.name}</li>
      ))}
    </ul>
  );
}

interface ChartViewProps {
  plan: ChartPlan;
  selection: ChartSelection;
  catalog: readonly MetricCategory[];
}

export function ChartView({ plan, selection, catalog }: ChartViewProps) {
  const { settings } = useLeagueSettings();
  const frame = useRef<HTMLDivElement>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const highlight = new Set(selection.players.map((p) => p.id));
  const playerKey = selection.players.map((p) => p.id).join(',');
  const [xId, yId] = selection.metrics;
  const xDef = xId ? findMetric(catalog, xId) : undefined;
  const yDef = yId ? findMetric(catalog, yId) : undefined;
  const statLabel = WEEKLY_STATS.find((s) => s.key === selection.weeklyStat)?.label ?? selection.weeklyStat;
  const scope = selection.position ? `${selection.position}s` : 'All players';

  const title =
    plan.kind === 'trend' ? `${statLabel} by week`
      : plan.kind === 'leaders' ? `${xDef?.label ?? 'Metric'} leaders`
        : `${xDef?.label ?? 'X'} vs. ${yDef?.label ?? 'Y'}`;

  const data = useAsync<ChartResult>(
    (signal) => {
      if (plan.kind === 'trend') {
        return loadTrend(selection.players.map((p) => p.id), selection.weeklyStat, settings, signal).then((series) => ({ kind: 'trend' as const, series }));
      }
      if (plan.kind === 'leaders') {
        return loadLeaders(xId, selection.position, highlight, signal).then((bars) => ({ kind: 'leaders' as const, bars }));
      }
      return loadScatter(xId, yId, selection.position, highlight, signal).then((points) => ({ kind: 'scatter' as const, points }));
    },
    [plan.kind, playerKey, selection.weeklyStat, xId, yId, selection.position, settings],
  );

  return (
    <section className="chart-card" aria-label={title}>
      <header className="chart-card__header">
        <div>
          <p className="chart-card__eyebrow">Auto chart · {plan.kind === 'trend' ? 'trend line' : plan.kind === 'leaders' ? 'ranked bars' : 'scatter'}</p>
          <h2 className="chart-card__title">{title}</h2>
          <p className="chart-card__reason">{plan.kind === 'trend' ? selection.players.map((p) => p.name).join(', ') : scope} · {plan.reason}</p>
        </div>
        <button type="button" className="btn btn--ghost btn--sm" disabled={data.status !== 'success'}
          onClick={() => downloadSvg(frame.current?.querySelector('svg') ?? null, slug(title))}>
          <Icon name="external" size={14} /> Download SVG
        </button>
      </header>
      <div ref={frame}>
        <AsyncView
          state={data}
          loading={<Skeleton lines={6} label="Building chart…" />}
          errorTitle="Couldn't build this chart"
          isEmpty={(result) => (result.kind === 'trend' ? result.series.every((s) => s.points.length === 0)
            : result.kind === 'leaders' ? result.bars.length === 0 : result.points.length === 0)}
          empty={<EmptyState title="No data for this selection.">Try another position, metric, or season.</EmptyState>}
        >
          {(result) => {
            if (result.kind === 'trend') {
              return (
                <>
                  <LineChart title={title} xLabel="Week" yLabel={statLabel} series={result.series}
                    description={`${statLabel} each week for ${result.series.map((s) => s.name).join(', ')}.`} />
                  <SeriesLegend series={result.series} />
                </>
              );
            }
            if (result.kind === 'leaders') {
              return <BarChart title={title} bars={result.bars} format={xDef?.format ?? 'decimal'}
                description={`Top ${LEADERS_SHOWN} ${scope} by ${xDef?.label ?? 'the metric'}.`} />;
            }
            const xAxis = { label: xDef?.label ?? 'X', format: xDef?.format ?? 'decimal' };
            const yAxis = { label: yDef?.label ?? 'Y', format: yDef?.format ?? 'decimal' };
            const selected = result.points.find((p) => p.id === selectedId);
            return (
              <>
                <ScatterChart title={title} points={result.points} xLabel={xAxis.label} yLabel={yAxis.label}
                  xFormat={xAxis.format} yFormat={yAxis.format}
                  onSelect={(pid) => setSelectedId((current) => (current === pid ? null : pid))} selectedId={selected?.id ?? null}
                  description={`${result.points.length} ${scope} plotted by ${xDef?.label} and ${yDef?.label}; dashed lines mark the medians. Hover a point for the name; click it for details.`} />
                {selected
                  ? <ScatterPointCard point={selected} points={result.points} x={xAxis} y={yAxis} onClose={() => setSelectedId(null)} />
                  : <p className="chart-card__hint">Hover a point to see who it is; click it for details.</p>}
              </>
            );
          }}
        </AsyncView>
      </div>
    </section>
  );
}
