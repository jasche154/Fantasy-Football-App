import type { MetricFormat } from '../../api/types';
import type { ScatterDatum } from '../../components/charts/ScatterChart';
import { PlayerLink } from '../../components/PlayerLink';
import { median } from '../../lib/chartScale';
import { formatMetric } from '../../lib/format';

interface Axis {
  label: string;
  format: MetricFormat;
}

interface ScatterPointCardProps {
  point: ScatterDatum;
  points: readonly ScatterDatum[];
  x: Axis;
  y: Axis;
  onClose: () => void;
}

/** 1-based rank among the plotted points, highest value first. */
export function rankAmong(value: number, values: readonly number[]): number {
  return values.filter((v) => v > value).length + 1;
}

/** Where the point sits relative to the median lines, in words. */
export function quadrantLabel(point: ScatterDatum, points: readonly ScatterDatum[], x: string, y: string): string {
  const highX = point.x >= median(points.map((p) => p.x));
  const highY = point.y >= median(points.map((p) => p.y));
  if (highX && highY) return `Above the median in both ${x} and ${y}.`;
  if (!highX && !highY) return `Below the median in both ${x} and ${y}.`;
  return highX ? `Above the median in ${x}, below in ${y}.` : `Above the median in ${y}, below in ${x}.`;
}

function MetricRow({ axis, value, rank, total }: { axis: Axis; value: number; rank: number; total: number }) {
  return (
    <div className="point-card__metric">
      <dt>{axis.label}</dt>
      <dd>
        <strong>{formatMetric(value, axis.format)}</strong>
        <span className="point-card__rank">#{rank} of {total} plotted</span>
      </dd>
    </div>
  );
}

/** Details for the scatter point the user clicked: who it is, both metrics with ranks, and the quadrant. */
export function ScatterPointCard({ point, points, x, y, onClose }: ScatterPointCardProps) {
  const player = point.player ?? { id: point.id, name: point.label, position: null, team: null };
  return (
    <aside className="point-card" aria-label={`${point.label} details`}>
      <div className="point-card__head">
        <PlayerLink player={player} />
        <button type="button" className="btn btn--ghost btn--sm" onClick={onClose} aria-label="Close player details">×</button>
      </div>
      <dl className="point-card__metrics">
        <MetricRow axis={x} value={point.x} rank={rankAmong(point.x, points.map((p) => p.x))} total={points.length} />
        <MetricRow axis={y} value={point.y} rank={rankAmong(point.y, points.map((p) => p.y))} total={points.length} />
      </dl>
      <p className="point-card__note">{quadrantLabel(point, points, x.label, y.label)}</p>
    </aside>
  );
}
