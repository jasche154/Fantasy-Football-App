import type { ReactNode } from 'react';

export interface TooltipRow {
  label: string;
  value: string;
}

interface ChartTooltipProps {
  /** The hovered point, in the chart's viewBox coordinates. */
  x: number;
  y: number;
  /** The viewBox size, so the tooltip can be placed in percentages and scale with the chart. */
  width: number;
  height: number;
  title: string;
  meta?: ReactNode;
  rows: readonly TooltipRow[];
  hint?: string;
}

const EDGE = 0.18;          // within 18% of a side, the tooltip anchors to that side instead of centering
const TOP_ROOM = 110;       // viewBox units needed above the point; otherwise it opens below

/** An HTML tooltip card over an SVG chart, pointing at the hovered point. */
export function ChartTooltip({ x, y, width, height, title, meta, rows, hint }: ChartTooltipProps) {
  const horizontal = x / width < EDGE ? 'start' : x / width > 1 - EDGE ? 'end' : 'center';
  const vertical = y < TOP_ROOM ? 'below' : 'above';
  return (
    <div
      className={`chart-tooltip chart-tooltip--${horizontal} chart-tooltip--${vertical}`}
      style={{ left: `${(x / width) * 100}%`, top: `${(y / height) * 100}%` }}
      role="presentation"
    >
      <div className="chart-tooltip__head">
        <span className="chart-tooltip__title">{title}</span>
        {meta && <span className="chart-tooltip__meta">{meta}</span>}
      </div>
      <dl className="chart-tooltip__rows">
        {rows.map((row) => (
          <div key={row.label} className="chart-tooltip__row">
            <dt>{row.label}</dt>
            <dd>{row.value}</dd>
          </div>
        ))}
      </dl>
      {hint && <p className="chart-tooltip__hint">{hint}</p>}
    </div>
  );
}
