import { useId } from 'react';
import type { MetricFormat } from '../../api/types';
import { extent, linearScale, median, niceTicks } from '../../lib/chartScale';
import { formatMetric } from '../../lib/format';

export interface ScatterDatum {
  id: number;
  label: string;
  x: number;
  y: number;
  isHighlighted?: boolean;
}

interface ScatterChartProps {
  title: string;
  description: string;
  points: readonly ScatterDatum[];
  xLabel: string;
  yLabel: string;
  xFormat: MetricFormat;
  yFormat: MetricFormat;
  /** How many points (furthest up and to the right) get a name label besides highlighted ones. */
  labelCount?: number;
}

const WIDTH = 720;
const HEIGHT = 440;
const PAD = { top: 20, right: 24, bottom: 46, left: 60 };

/** The points to label: highlighted players plus the strongest combined x/y ranks. */
export function pointsToLabel(points: readonly ScatterDatum[], count: number): Set<number> {
  const byX = [...points].sort((a, b) => b.x - a.x).map((p) => p.id);
  const byY = [...points].sort((a, b) => b.y - a.y).map((p) => p.id);
  const score = (p: ScatterDatum) => byX.indexOf(p.id) + byY.indexOf(p.id);
  const top = [...points].sort((a, b) => score(a) - score(b)).slice(0, count).map((p) => p.id);
  return new Set([...top, ...points.filter((p) => p.isHighlighted).map((p) => p.id)]);
}

export interface PlacedLabel {
  id: number;
  x: number;
  y: number;
  anchor: 'start' | 'end';
}

const CHAR_WIDTH = 6.4;
const LABEL_HEIGHT = 13;
const LABEL_GAP = 8;

/**
 * Greedy label placement in priority order: right of the dot, else left, else skipped
 * (the dot keeps its tooltip). Boxes are estimated from the label length.
 */
export function placeLabels(candidates: readonly { id: number; x: number; y: number; text: string }[]): PlacedLabel[] {
  const boxes: { x0: number; x1: number; y0: number; y1: number }[] = [];
  const placed: PlacedLabel[] = [];
  for (const label of candidates) {
    const width = label.text.length * CHAR_WIDTH;
    const options = [
      { anchor: 'start' as const, x0: label.x + LABEL_GAP, x1: label.x + LABEL_GAP + width },
      { anchor: 'end' as const, x0: label.x - LABEL_GAP - width, x1: label.x - LABEL_GAP },
    ];
    const y0 = label.y - LABEL_HEIGHT / 2;
    const y1 = label.y + LABEL_HEIGHT / 2;
    const fit = options.find((o) => !boxes.some((b) => o.x0 < b.x1 && o.x1 > b.x0 && y0 < b.y1 && y1 > b.y0));
    if (!fit) continue;
    boxes.push({ x0: fit.x0, x1: fit.x1, y0, y1 });
    placed.push({ id: label.id, x: fit.anchor === 'start' ? fit.x0 : fit.x1, y: label.y, anchor: fit.anchor });
  }
  return placed;
}

/** Two-metric scatter with median guide lines splitting it into quadrants. */
export function ScatterChart({ title, description, points, xLabel, yLabel, xFormat, yFormat, labelCount = 8 }: ScatterChartProps) {
  const id = useId();
  const xTicks = niceTicks(...extent(points.map((p) => p.x)));
  const yTicks = niceTicks(...extent(points.map((p) => p.y)));
  const x = linearScale([xTicks[0], xTicks[xTicks.length - 1]], [PAD.left, WIDTH - PAD.right]);
  const y = linearScale([yTicks[0], yTicks[yTicks.length - 1]], [HEIGHT - PAD.bottom, PAD.top]);
  const labelled = pointsToLabel(points, labelCount);
  const byId = new Map(points.map((p) => [p.id, p]));
  const labelOrder = [...labelled].flatMap((pid) => byId.get(pid) ?? [])
    .sort((a, b) => Number(Boolean(b.isHighlighted)) - Number(Boolean(a.isHighlighted)));   // stable: rank order kept
  const labels = new Map(placeLabels(labelOrder.map((p) => ({ id: p.id, x: x(p.x), y: y(p.y), text: p.label })))
    .map((label) => [label.id, label]));
  const medianX = x(median(points.map((p) => p.x)));
  const medianY = y(median(points.map((p) => p.y)));

  return (
    <svg className="auto-chart" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-labelledby={`${id}-t ${id}-d`}>
      <title id={`${id}-t`}>{title}</title>
      <desc id={`${id}-d`}>{description}</desc>
      <g className="auto-chart__grid">
        {yTicks.map((tick) => (
          <g key={`y${tick}`}>
            <line x1={PAD.left} x2={WIDTH - PAD.right} y1={y(tick)} y2={y(tick)} />
            <text x={PAD.left - 8} y={y(tick)} dy="0.32em" textAnchor="end">{formatMetric(tick, yFormat)}</text>
          </g>
        ))}
        {xTicks.map((tick) => (
          <text key={`x${tick}`} x={x(tick)} y={HEIGHT - PAD.bottom + 18} textAnchor="middle">{formatMetric(tick, xFormat)}</text>
        ))}
      </g>
      <line className="auto-chart__median" x1={medianX} x2={medianX} y1={PAD.top} y2={HEIGHT - PAD.bottom} />
      <line className="auto-chart__median" x1={PAD.left} x2={WIDTH - PAD.right} y1={medianY} y2={medianY} />
      <text className="auto-chart__axis" x={(PAD.left + WIDTH - PAD.right) / 2} y={HEIGHT - 6} textAnchor="middle">{xLabel}</text>
      <text className="auto-chart__axis" transform={`translate(16 ${(PAD.top + HEIGHT - PAD.bottom) / 2}) rotate(-90)`} textAnchor="middle">{yLabel}</text>
      {points.map((point) => (
        <g key={point.id} className={point.isHighlighted ? 'auto-chart__point is-highlighted' : 'auto-chart__point'}>
          <circle cx={x(point.x)} cy={y(point.y)} r={point.isHighlighted ? 6 : 4.5}>
            <title>{`${point.label}: ${xLabel} ${formatMetric(point.x, xFormat)}, ${yLabel} ${formatMetric(point.y, yFormat)}`}</title>
          </circle>
          {labels.has(point.id) && (
            <text className="auto-chart__point-label" x={labels.get(point.id)?.x} y={labels.get(point.id)?.y}
              dy="0.32em" textAnchor={labels.get(point.id)?.anchor}>{point.label}</text>
          )}
        </g>
      ))}
    </svg>
  );
}
