const CHAR_WIDTH = 6.6;
const PAD_X = 7;
const HEIGHT = 22;
const OFFSET = 12;

interface HoverTagProps {
  /** The point's position in chart coordinates. */
  x: number;
  y: number;
  text: string;
  /** Chart bounds, so the tag flips instead of running off an edge. */
  width: number;
  top: number;
}

/** A name tag drawn above a hovered or focused point (below it when there's no room). */
export function HoverTag({ x, y, text, width, top }: HoverTagProps) {
  const boxWidth = text.length * CHAR_WIDTH + PAD_X * 2;
  const left = Math.min(Math.max(x - boxWidth / 2, 2), width - boxWidth - 2);
  const above = y - OFFSET - HEIGHT >= top;
  const boxTop = above ? y - OFFSET - HEIGHT : y + OFFSET;
  return (
    <g className="auto-chart__hover" pointerEvents="none" aria-hidden="true">
      <rect x={left} y={boxTop} width={boxWidth} height={HEIGHT} rx={5} />
      <text x={left + boxWidth / 2} y={boxTop + HEIGHT / 2} dy="0.34em" textAnchor="middle">{text}</text>
    </g>
  );
}
