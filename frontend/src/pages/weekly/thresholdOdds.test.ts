import { describe, expect, it } from 'vitest';
import { oddsLabel, oddsPercent } from './thresholdOdds';

describe('threshold odds labels', () => {
  it('never rounds a long shot to 0% or a near lock to 100%', () => {
    expect([oddsPercent(0), oddsPercent(0.004), oddsPercent(0.31), oddsPercent(0.996), oddsPercent(1)])
      .toEqual(['0%', '<1%', '31%', '>99%', '100%']);
    expect(oddsLabel({ points: 20, probability: 0.31 })).toBe('20+ pts: 31%');
  });
});
