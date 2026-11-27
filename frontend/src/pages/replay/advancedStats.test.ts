import { describe, expect, it } from 'vitest';
import { boxTotals, gamesPlayed, mean, rate, seasonSummary, trend } from './advancedStats';
import { advancedDetail, boxLine } from './fixturesPlayers';

describe('rate and mean', () => {
  it('divides, and gives null instead of dividing by zero', () => {
    expect(rate(6, 8)).toBe(0.75);
    expect(rate(3, 0)).toBeNull();
  });

  it('averages the numbers present', () => {
    expect(mean([2, null, 4])).toBe(3);
    expect(mean([null])).toBeNull();
    expect(mean([])).toBeNull();
  });
});

describe('seasonSummary', () => {
  it('averages over the games he played, skipping the bye and the week still to play', () => {
    const { weeks } = advancedDetail();
    expect(gamesPlayed(weeks).map((week) => week.week)).toEqual([1, 2, 4, 5, 6]);
    const summary = seasonSummary(weeks);
    expect(summary.games).toBe(5);
    expect(summary.snapPct).toBeCloseTo(79.4);
    expect(summary.targetShare).toBeCloseTo(0.208);
    expect(summary.targetsPerGame).toBeCloseTo(6.4);
    expect(summary.carriesPerGame).toBeCloseTo(0.2);
    expect(summary.catchPct).toBeCloseTo(22 / 32);
    expect(summary.ydsPerTarget).toBeCloseTo(260 / 32);
    expect(summary.ydsPerCarry).toBe(5);
  });

  it('takes the completion rate from the games that recorded attempts', () => {
    const [first, second] = gamesPlayed(advancedDetail().weeks);
    const recorded = { ...first, box: boxLine({ pass_cmp: 22, pass_att: 34, pass_yds: 250 }) };
    const unrecorded = { ...second, box: boxLine({ pass_cmp: null, pass_att: null, pass_yds: 300 }) };
    expect(seasonSummary([recorded, unrecorded]).cmpPct).toBeCloseTo(22 / 34);
    expect(seasonSummary([unrecorded]).cmpPct).toBeNull();
  });

  it('is all empty before he has played', () => {
    expect(seasonSummary([])).toEqual({ games: 0, snapPct: null, targetShare: null, targetsPerGame: null, carriesPerGame: null,
      catchPct: null, ydsPerTarget: null, ydsPerCarry: null, cmpPct: null });
  });

  it('adds box lines stat by stat', () => {
    expect(boxTotals([boxLine(), boxLine({ rec: 1 })])).toMatchObject({ carries: 30, rec: 4, rush_td: 2 });
  });
});

describe('trend', () => {
  it('compares the last 3 games with the season', () => {
    expect(trend([70, 72, 80, 85, 90], 3)).toEqual({ recent: 85, season: 79.4, direction: 'up' });
    expect(trend([90, 85, 80, 72, 70], 3)?.direction).toBe('down');
    expect(trend([80, 80, 81, 79, 80], 3)?.direction).toBe('flat');
  });

  it('needs more games than the recent window, ignoring missing values', () => {
    expect(trend([70, 80, 90], 3)).toBeNull();
    expect(trend([70, null, 80, 90], 3)).toBeNull();
  });
});
