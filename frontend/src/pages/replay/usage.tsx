import type { ReplayUsage } from '../../api/types';
import { formatFraction, formatNumber, formatPercentValue } from '../../lib/format';

/** One line for the player card: "Last 3 games: 72% snaps, 6.3 targets (24% share), 0.7 carries a game". */
export function usageLine(usage: ReplayUsage): string {
  const games = usage.games === 1 ? 'Last game' : `Last ${usage.games} games`;
  return `${games}: ${formatPercentValue(usage.snap_pct, 0)} snaps, ${formatNumber(usage.targets)} targets `
    + `(${formatFraction(usage.target_share, 0)} share), ${formatNumber(usage.carries)} carries a game`;
}
