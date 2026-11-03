import type { ThresholdOdds } from '../../api/types';
import { formatFraction, formatNumber } from '../../lib/format';

const BARELY = 0.01;

/** "31%", with "<1%" for long shots that would otherwise read as impossible. */
export function oddsPercent(probability: number): string {
  return probability > 0 && probability < BARELY ? '<1%' : formatFraction(probability, 0);
}

/** "20+ pts: 31%". */
export function oddsLabel(odds: ThresholdOdds): string {
  return `${formatNumber(odds.points, 0)}+ pts: ${oddsPercent(odds.probability)}`;
}
