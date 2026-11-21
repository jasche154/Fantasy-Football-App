import type { ReplayUsage } from '../../api/types';
import { EMPTY_VALUE, formatFraction, formatNumber, formatPercentValue } from '../../lib/format';

const COLUMNS = [
  { label: 'Snap %', title: 'Share of offensive snaps' },
  { label: 'Tgt', title: 'Targets per game' },
  { label: 'Tgt %', title: 'Share of team targets' },
  { label: 'Car', title: 'Carries per game' },
] as const;

/** The four usage headers; `span` says what they average ("last 3 games", "that week"). */
export function UsageHeaders({ span }: { span: string }) {
  return COLUMNS.map(({ label, title }) => (
    <th key={label} scope="col" className="num th--end"><abbr title={`${title}, ${span}`}>{label}</abbr></th>
  ));
}

/** Snap %, Tgt, Tgt % and Car; a dash for a missing number (no game, K/DST, no snap data). */
export function usageTexts(usage: ReplayUsage | null): string[] {
  if (!usage) return COLUMNS.map(() => EMPTY_VALUE);
  return [formatPercentValue(usage.snap_pct, 0), formatNumber(usage.targets), formatFraction(usage.target_share, 0), formatNumber(usage.carries)];
}

export function UsageCells({ usage }: { usage: ReplayUsage | null }) {
  return usageTexts(usage).map((text, index) => <td key={COLUMNS[index].label} className="num">{text}</td>);
}

/** One line for a header: "Last 3 games: 72% snaps, 6.3 targets (24% share), 0.0 carries". */
export function usageLine(usage: ReplayUsage): string {
  const [snaps, targets, share, carries] = usageTexts(usage);
  const games = usage.games === 1 ? 'Last game' : `Last ${usage.games} games`;
  return `${games}: ${snaps} snaps, ${targets} targets (${share} share), ${carries} carries a game`;
}
