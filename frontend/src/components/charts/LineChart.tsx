import { useId, useState } from 'react';
import { extent, linearScale, niceTicks, seriesClass } from '../../lib/chartScale';
import { formatNumber } from '../../lib/format';
import { HoverTag } from './HoverTag';

export interface LinePoint {
  x: number;
  y: number;
  label?: string;
}

export interface LineSeries {
  name: string;
  points: readonly LinePoint[];
}

interface LineChartProps {
  title: string;
  description: string;
  series: readonly LineSeries[];
  xLabel: string;
  yLabel: string;
  digits?: number;
}

const WIDTH = 720;
const HEIGHT = 320;
const PAD = { top: 16, right: 20, bottom: 40, left: 52 };

/** SVG path through the points; a gap in x (a missed week) starts a new segment. */
export function linePath(points: readonly LinePoint[], x: (v: number) => number, y: (v: number) => number): string {
  return points
    .map((p, i) => `${i === 0 || p.x - points[i - 1].x > 1 ? 'M' : 'L'}${x(p.x).toFixed(1)},${y(p.y).toFixed(1)}`)
    .join(' ');
}

/** Multi-series line chart (e.g. weekly fantasy points for up to five players). */
export function LineChart({ title, description, series, xLabel, yLabel, digits = 1 }: LineChartProps) {
  const id = useId();
  const [hovered, setHovered] = useState<{ x: number; y: number; text: string } | null>(null);
  const allPoints = series.flatMap((s) => s.points);
  const [xMin, xMax] = extent(allPoints.map((p) => p.x));
  const [yMinRaw, yMaxRaw] = extent(allPoints.map((p) => p.y));
  const yTicks = niceTicks(Math.min(0, yMinRaw), yMaxRaw);
  const x = linearScale([xMin, xMax], [PAD.left, WIDTH - PAD.right]);
  const y = linearScale([yTicks[0], yTicks[yTicks.length - 1]], [HEIGHT - PAD.bottom, PAD.top]);
  const xTicks = Array.from({ length: xMax - xMin + 1 }, (_, index) => xMin + index);

  return (
    <svg className="auto-chart" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-labelledby={`${id}-t ${id}-d`}>
      <title id={`${id}-t`}>{title}</title>
      <desc id={`${id}-d`}>{description}</desc>
      <g className="auto-chart__grid">
        {yTicks.map((tick) => (
          <g key={tick}>
            <line x1={PAD.left} x2={WIDTH - PAD.right} y1={y(tick)} y2={y(tick)} />
            <text x={PAD.left - 8} y={y(tick)} dy="0.32em" textAnchor="end">{formatNumber(tick, 0)}</text>
          </g>
        ))}
        {xTicks.map((tick) => (
          <text key={tick} x={x(tick)} y={HEIGHT - PAD.bottom + 18} textAnchor="middle">{tick}</text>
        ))}
      </g>
      <text className="auto-chart__axis" x={(PAD.left + WIDTH - PAD.right) / 2} y={HEIGHT - 4} textAnchor="middle">{xLabel}</text>
      <text className="auto-chart__axis" transform={`translate(14 ${(PAD.top + HEIGHT - PAD.bottom) / 2}) rotate(-90)`} textAnchor="middle">{yLabel}</text>
      {series.map((item, index) => {
        const path = linePath(item.points, x, y);
        return (
          <g key={item.name} className={`auto-chart__series ${seriesClass(index)}`}>
            <path className="auto-chart__line" d={path} />
            {item.points.map((p) => (
              <g key={p.x} onMouseEnter={() => setHovered({ x: x(p.x), y: y(p.y),
                text: `${item.name} · ${xLabel} ${p.x}: ${formatNumber(p.y, digits)}` })}
                onMouseLeave={() => setHovered(null)}>
                <circle className="auto-chart__dot-hit" cx={x(p.x)} cy={y(p.y)} r={9} />
                <circle className="auto-chart__dot" cx={x(p.x)} cy={y(p.y)} r={4}>
                  <title>{`${item.name}, ${xLabel.toLowerCase()} ${p.x}${p.label ? ` ${p.label}` : ''}: ${formatNumber(p.y, digits)}`}</title>
                </circle>
              </g>
            ))}
          </g>
        );
      })}
      {hovered && <HoverTag x={hovered.x} y={hovered.y} text={hovered.text} width={WIDTH} top={PAD.top} />}
    </svg>
  );
}
