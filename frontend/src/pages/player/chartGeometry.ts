import type { GameLogRow } from '../../api/types';
import { formatNumber } from '../../lib/format';

export interface ChartDims {
  width: number;
  height: number;
  padding: { top: number; right: number; bottom: number; left: number };
}

export const DEFAULT_CHART_DIMS: ChartDims = {
  width: 640,
  height: 260,
  padding: { top: 16, right: 12, bottom: 30, left: 36 },
};

export interface ChartBar {
  key: string;
  week: number;
  x: number;
  y: number;
  width: number;
  height: number;
  centerX: number;
  value: number;
  expected: number | null;
  opponent: string | null;
}

export interface ChartPoint {
  key: string;
  x: number;
  y: number;
  value: number;
}

export interface ChartGeometry {
  bars: ChartBar[];
  points: ChartPoint[];
  /** SVG path for the expected-points line; breaks where expected points are missing. */
  linePath: string;
  ticks: Array<{ value: number; y: number }>;
  zeroY: number;
  dims: ChartDims;
}

/** Rounds a raw axis step up to 1, 2, 2.5 or 5 times a power of ten. */
export function niceStep(raw: number): number {
  if (!Number.isFinite(raw) || raw <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const fraction = raw / magnitude;
  const nice = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 2.5 ? 2.5 : fraction <= 5 ? 5 : 10;
  return nice * magnitude;
}

interface Scale {
  top: number;
  bottom: number;
  step: number;
  toY: (value: number) => number;
}

function buildScale(values: number[], dims: ChartDims): Scale {
  const max = Math.max(1, ...values);
  const min = Math.min(0, ...values);
  const step = niceStep((max - min) / 4);
  const top = Math.ceil(max / step) * step;
  const bottom = Math.floor(min / step) * step;
  const innerHeight = dims.height - dims.padding.top - dims.padding.bottom;
  const toY = (value: number) => dims.padding.top + ((top - value) / (top - bottom)) * innerHeight;
  return { top, bottom, step, toY };
}

function buildTicks(scale: Scale): Array<{ value: number; y: number }> {
  const count = Math.round((scale.top - scale.bottom) / scale.step);
  return Array.from({ length: count + 1 }, (_, index) => {
    const value = Number((scale.bottom + index * scale.step).toFixed(4));
    return { value, y: scale.toY(value) };
  });
}

function buildLinePath(points: Array<ChartPoint | null>): string {
  return points
    .reduce<{ path: string[]; isDrawing: boolean }>(
      (acc, point) => {
        if (!point) return { path: acc.path, isDrawing: false };
        const command = `${acc.isDrawing ? 'L' : 'M'}${point.x.toFixed(1)},${point.y.toFixed(1)}`;
        return { path: [...acc.path, command], isDrawing: true };
      },
      { path: [], isDrawing: false },
    )
    .path.join(' ');
}

export function buildChartGeometry(rows: readonly GameLogRow[], dims: ChartDims = DEFAULT_CHART_DIMS): ChartGeometry {
  const values = rows.flatMap((row) =>
    row.expected_points === null ? [row.fantasy_points] : [row.fantasy_points, row.expected_points],
  );
  const scale = buildScale(values, dims);
  const zeroY = scale.toY(0);
  const innerWidth = dims.width - dims.padding.left - dims.padding.right;
  const band = rows.length > 0 ? innerWidth / rows.length : innerWidth;
  const barWidth = Math.min(band * 0.62, 40);
  const bars = rows.map((row, index) => {
    const centerX = dims.padding.left + index * band + band / 2;
    const y = scale.toY(row.fantasy_points);
    return {
      key: `${row.season_type ?? 'REG'}-${row.week}`,
      week: row.week,
      x: centerX - barWidth / 2,
      y: Math.min(y, zeroY),
      width: barWidth,
      height: Math.max(Math.abs(zeroY - y), 1),
      centerX,
      value: row.fantasy_points,
      expected: row.expected_points,
      opponent: row.opponent,
    };
  });
  const maybePoints = bars.map((bar) =>
    bar.expected === null ? null : { key: bar.key, x: bar.centerX, y: scale.toY(bar.expected), value: bar.expected },
  );
  const points = maybePoints.filter((point): point is ChartPoint => point !== null);
  return { bars, points, linePath: buildLinePath(maybePoints), ticks: buildTicks(scale), zeroY, dims };
}

/** Plain-language summary used as the chart's <desc>. */
export function describeChart(name: string, season: number, rows: readonly GameLogRow[]): string {
  if (rows.length === 0) return `No games logged for ${name} in ${season}.`;
  const best = rows.reduce((top, row) => (row.fantasy_points > top.fantasy_points ? row : top), rows[0]);
  const total = rows.reduce((sum, row) => sum + row.fantasy_points, 0);
  const games = rows.length === 1 ? '1 game' : `${rows.length} games`;
  return (
    `Weekly fantasy points for ${name} in ${season} across ${games}, averaging ${formatNumber(total / rows.length)}. ` +
    `Best week: week ${best.week} with ${formatNumber(best.fantasy_points)} points. ` +
    'Bars show actual points; the line with dots shows expected points from opportunity.'
  );
}
