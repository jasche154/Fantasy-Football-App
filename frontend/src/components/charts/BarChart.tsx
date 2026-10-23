import { useId } from 'react';
import { linearScale, niceTicks } from '../../lib/chartScale';
import { formatMetric } from '../../lib/format';
import type { MetricFormat } from '../../api/types';

export interface BarDatum {
  label: string;
  sublabel?: string;
  value: number;
  isHighlighted?: boolean;
}

interface BarChartProps {
  title: string;
  description: string;
  bars: readonly BarDatum[];
  format: MetricFormat;
}

const WIDTH = 720;
const ROW = 26;
const PAD = { top: 12, right: 64, bottom: 28, left: 190 };

/** Ranked horizontal bars (leaders for one metric). Negative values extend left of zero. */
export function BarChart({ title, description, bars, format }: BarChartProps) {
  const id = useId();
  const height = PAD.top + PAD.bottom + bars.length * ROW;
  const values = bars.map((b) => b.value);
  const ticks = niceTicks(Math.min(0, ...values), Math.max(0, ...values), 5);
  const x = linearScale([ticks[0], ticks[ticks.length - 1]], [PAD.left, WIDTH - PAD.right]);

  return (
    <svg className="auto-chart" viewBox={`0 0 ${WIDTH} ${height}`} role="img" aria-labelledby={`${id}-t ${id}-d`}>
      <title id={`${id}-t`}>{title}</title>
      <desc id={`${id}-d`}>{description}</desc>
      <g className="auto-chart__grid">
        {ticks.map((tick) => (
          <g key={tick}>
            <line x1={x(tick)} x2={x(tick)} y1={PAD.top} y2={height - PAD.bottom} />
            <text x={x(tick)} y={height - PAD.bottom + 16} textAnchor="middle">{formatMetric(tick, format)}</text>
          </g>
        ))}
      </g>
      {bars.map((bar, index) => {
        const top = PAD.top + index * ROW;
        const start = x(Math.min(0, bar.value));
        const width = Math.abs(x(bar.value) - x(0));
        return (
          <g key={`${bar.label}-${index}`} className={bar.isHighlighted ? 'auto-chart__bar is-highlighted' : 'auto-chart__bar'}>
            <text className="auto-chart__label" x={PAD.left - 10} y={top + ROW / 2} dy="0.32em" textAnchor="end">
              {bar.label}
              {bar.sublabel && <tspan className="auto-chart__sublabel"> {bar.sublabel}</tspan>}
            </text>
            <rect x={start} y={top + 4} width={Math.max(width, 1)} height={ROW - 8} rx={3}>
              <title>{`${bar.label}: ${formatMetric(bar.value, format)}`}</title>
            </rect>
            <text className="auto-chart__value" x={Math.max(x(bar.value), x(0)) + 6} y={top + ROW / 2} dy="0.32em">
              {formatMetric(bar.value, format)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
