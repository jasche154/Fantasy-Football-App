import { describe, expect, it } from 'vitest';
import type { ReplayVerdict } from '../../api/types';
import { totalsNote } from './TradeParts';

const PLAYER = { id: 1, name: 'Chris Olave', position: 'WR', team: 'NO' } as ReplayVerdict['give'][number]['player'];

function verdict(overrides: Partial<ReplayVerdict>): ReplayVerdict {
  return {
    accepted: true, reasons: [], fair_label: 'fair', value_give: 500, value_get: 575.1, percent: 0.1, your_gain: 0,
    their_gain: 0, give: [{ player: PLAYER, value: 500 }], get: [{ player: PLAYER, value: 575.1 }], your_drops: [],
    their_drops: [], suggestion: null, ...overrides,
  };
}

describe('totalsNote', () => {
  it('names the best-player premium a total adds to the players own values', () => {
    expect(totalsNote(verdict({ value_get: 597.7, premium: 22.6 })))
      .toBe('The totals: what you get includes +23 for the best player in the deal.');
    expect(totalsNote(verdict({ value_give: 540, premium: -40 })))
      .toBe('The totals: what you give includes +40 for the best player in the deal.');
  });

  it('names what the user would drop and says nothing when the totals are the players values', () => {
    expect(totalsNote(verdict({ value_get: 525.1, premium: 0 })))
      .toBe('The totals: what you get is less 50 for the players you would drop.');
    expect(totalsNote(verdict({}))).toBeNull();
  });
});
