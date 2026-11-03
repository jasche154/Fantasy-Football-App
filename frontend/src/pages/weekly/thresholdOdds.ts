import type { ThresholdOdds } from '../../api/types';
import { formatFraction, formatNumber } from '../../lib/format';

const BARELY = 0.01;

/** "31%", with "<1%" and ">99%" for odds that would otherwise round to impossible or certain. */
export function oddsPercent(probability: number): string {
  if (probability > 0 && probability < BARELY) return '<1%';
  if (probability > 1 - BARELY && probability < 1) return '>99%';
  return formatFraction(probability, 0);
}

/** "20+ pts: 31%". */
export function oddsLabel(odds: ThresholdOdds): string {
  return `${formatNumber(odds.points, 0)}+ pts: ${oddsPercent(odds.probability)}`;
}
