import { useId, useMemo } from 'react';
import type { GameLogRow } from '../../api/types';
import { formatNumber } from '../../lib/format';
import { buildChartGeometry, describeChart, type ChartBar } from './chartGeometry';

interface WeeklyChartProps {
  rows: readonly GameLogRow[];
  playerName: string;
  season: number;
}

function barTitle(bar: ChartBar): string {
  const opponent = bar.opponent ? ` vs ${bar.opponent}` : '';
  const expected = bar.expected === null ? 'no expected points' : `expected ${formatNumber(bar.expected)}`;
  return `Week ${bar.week}${opponent}: ${formatNumber(bar.value)} points (${expected})`;
}

export function WeeklyChart({ rows, playerName, season }: WeeklyChartProps) {
  const id = useId();
  const geometry = useMemo(() => buildChartGeometry(rows), [rows]);
  const { dims } = geometry;
  return (
    <figure className="chart">
      <svg
        className="chart__svg"
        viewBox={`0 0 ${dims.width} ${dims.height}`}
        role="img"
        aria-labelledby={`${id}-title ${id}-desc`}
        preserveAspectRatio="xMidYMid meet"
      >
        <title id={`${id}-title`}>{`${playerName} weekly fantasy points, ${season}`}</title>
        <desc id={`${id}-desc`}>{describeChart(playerName, season, rows)}</desc>
        <g className="chart__grid">
          {geometry.ticks.map((tick) => (
            <g key={tick.value}>
              <line x1={dims.padding.left} x2={dims.width - dims.padding.right} y1={tick.y} y2={tick.y} />
              <text x={dims.padding.left - 6} y={tick.y} dy="0.32em" textAnchor="end" className="chart__tick">
                {tick.value}
              </text>
            </g>
          ))}
        </g>
        <line className="chart__zero" x1={dims.padding.left} x2={dims.width - dims.padding.right} y1={geometry.zeroY} y2={geometry.zeroY} />
        {geometry.bars.map((bar) => (
          <g key={bar.key}>
            <rect className={bar.value < 0 ? 'chart__bar chart__bar--neg' : 'chart__bar'} x={bar.x} y={bar.y} width={bar.width} height={bar.height} rx={3}>
              <title>{barTitle(bar)}</title>
            </rect>
            <text x={bar.centerX} y={dims.height - dims.padding.bottom + 16} textAnchor="middle" className="chart__tick">
              {bar.week}
            </text>
          </g>
        ))}
        {geometry.linePath && <path className="chart__line" d={geometry.linePath} />}
        {geometry.points.map((point) => (
          <circle key={point.key} className="chart__dot" cx={point.x} cy={point.y} r={3.5} />
        ))}
      </svg>
      <figcaption className="chart__legend">
        <span className="legend-item"><span className="legend-swatch legend-swatch--bar" aria-hidden="true" />Actual points (bars)</span>
        <span className="legend-item"><span className="legend-swatch legend-swatch--line" aria-hidden="true" />Expected points (line)</span>
        <span className="muted">Week number on the horizontal axis</span>
      </figcaption>
    </figure>
  );
}
