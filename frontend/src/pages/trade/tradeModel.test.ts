import { describe, expect, it } from 'vitest';
import type { TradeSidePlayer } from '../../api/types';
import { BIJAN, CHASE } from '../../test/fixtures';
import { offerHash, offerTitle } from './TradeFinder';
import { marketText, PACKAGE_DISCOUNT_NOTE, playerContext } from './TradeResultView';
import { parseTradeQuery, swapSides, tradeQueryParams, withLeague } from './tradeQuery';

const entry = (overrides: Partial<TradeSidePlayer> = {}): TradeSidePlayer => ({
  player: CHASE, trade_value: 40, ros_ppg: 18.4, vorp_per_game: 4, weight: 1, adjusted_value: 40, ...overrides,
});

describe('trade query league', () => {
  it('reads and writes the league a trade is scored on', () => {
    expect(parseTradeQuery({ give: '1', get: '2', league: '7' })).toEqual({ give: [1], get: [2], league: 7 });
    expect(parseTradeQuery({ give: '1', league: 'abc' })).toEqual({ give: [1], get: [] });
    expect(tradeQueryParams({ give: [1], get: [2], league: 7 })).toEqual({ give: '1', get: '2', league: '7' });
  });

  it('keeps the league through a swap and drops it on request', () => {
    expect(swapSides({ give: [1], get: [2], league: 3 })).toEqual({ give: [2], get: [1], league: 3 });
    expect(withLeague({ give: [1], get: [2], league: 3 }, undefined)).toEqual({ give: [1], get: [2] });
  });
});

describe('trade result copy', () => {
  it('describes the package discount the backend applies', () => {
    expect(PACKAGE_DISCOUNT_NOTE).toContain('70%');
    expect(PACKAGE_DISCOUNT_NOTE).not.toContain('90%');
  });

  it('explains a value with rank, games left, chance to play and schedule', () => {
    expect(playerContext(entry({ pos_rank: 'WR3', games_left: 8, availability: 0.6, schedule_factor: 1.1 })))
      .toBe('WR3 · 18.4 PPG · 8 games left · plays about 60% · easy schedule');
    expect(playerContext(entry({ games_left: 1, availability: 1, schedule_factor: 1.01 }))).toBe('18.4 PPG · 1 game left');
  });

  it('explains an injury discount and a fill-in who played well', () => {
    expect(playerContext(entry({ availability: 0.72, injury_note: 'Knee: likely out 2 more games', fill_in: 'Braelon Allen' })))
      .toBe('18.4 PPG · Knee: likely out 2 more games · worth about 72% of healthy · may share work with Braelon Allen, who played well while he was out');
    expect(playerContext(entry({ availability: 0.98, injury_note: '1 game back from injury' })))
      .toBe('18.4 PPG · 1 game back from injury');
    expect(playerContext(entry({ availability: 0, injury_note: 'Out for the season' }))).toBe('18.4 PPG · Out for the season');
    expect(playerContext(entry({ injury_note: 'Knee: likely out 1 more game' }))).toBe('18.4 PPG · Knee: likely out 1 more game');
    expect(playerContext(entry({ fill_in: 'Braelon Allen' })))
      .toBe('18.4 PPG · may share work with Braelon Allen, who played well while he was out');
  });

  it('summarizes the market view', () => {
    expect(marketText(null)).toBeNull();
    expect(marketText(0.03)).toBe('Expert consensus also sees this as about even.');
    expect(marketText(-0.12)).toBe('By expert consensus it favours the other side by 12%.');
  });

  it('links offers into the calculator with their league', () => {
    const offer = { give: [CHASE], get: BIJAN };
    expect(offerTitle(offer)).toBe(`${CHASE.name} for ${BIJAN.name}`);
    expect(offerHash(offer, 4)).toBe(`#/trade?give=${CHASE.id}&get=${BIJAN.id}&league=4`);
    expect(offerHash(offer)).toBe(`#/trade?give=${CHASE.id}&get=${BIJAN.id}`);
  });
});
