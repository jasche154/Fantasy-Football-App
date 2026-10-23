import { describe, expect, it } from 'vitest';
import { BIJAN, CHASE, KELCE, tradeResult } from '../../test/fixtures';
import { balanceText, toBalancePercent } from './BalanceMeter';
import {
  addToSide,
  canEvaluate,
  MAX_PLAYERS_PER_SIDE,
  parseIdList,
  parseTradeQuery,
  playersInResult,
  rememberPlayers,
  removeFromSide,
  swapSides,
  tradeQueryParams,
} from './tradeQuery';

describe('trade query helpers', () => {
  it('parses id lists defensively', () => {
    expect(parseIdList('1, 2,abc,2,3,-4,0')).toEqual([1, 2, 3]);
    expect(parseIdList(undefined)).toEqual([]);
    expect(parseIdList('')).toEqual([]);
  });

  it('caps each side so shared links cannot fan out into huge request bursts', () => {
    const many = Array.from({ length: 25 }, (_, index) => index + 1).join(',');
    expect(parseIdList(many)).toHaveLength(MAX_PLAYERS_PER_SIDE);
    const full = { give: parseIdList(many), get: [] };
    expect(addToSide(full, 'give', 99)).toBe(full);
  });

  it('keeps a player on only one side', () => {
    expect(parseTradeQuery({ give: '1,2', get: '2,3' })).toEqual({ give: [1, 2], get: [3] });
  });

  it('serialises back to query params', () => {
    expect(tradeQueryParams({ give: [1, 2], get: [] })).toEqual({ give: '1,2', get: undefined });
  });

  it('adds, removes and swaps immutably', () => {
    const start = { give: [1], get: [2] };
    const added = addToSide(start, 'get', 3);
    expect(added).toEqual({ give: [1], get: [2, 3] });
    expect(start).toEqual({ give: [1], get: [2] });
    expect(addToSide(added, 'give', 3)).toBe(added);
    expect(removeFromSide(added, 'get', 2)).toEqual({ give: [1], get: [3] });
    expect(swapSides(start)).toEqual({ give: [2], get: [1] });
  });

  it('only evaluates when both sides have players', () => {
    expect(canEvaluate({ give: [1], get: [] })).toBe(false);
    expect(canEvaluate({ give: [1], get: [2] })).toBe(true);
  });

  it('remembers new players without replacing the map needlessly', () => {
    const known = rememberPlayers({}, [BIJAN]);
    expect(known).toEqual({ 1: BIJAN });
    expect(rememberPlayers(known, [BIJAN])).toBe(known);
    expect(Object.keys(rememberPlayers(known, [CHASE, KELCE]))).toEqual(['1', '2', '3']);
  });

  it('lists every player named in a result', () => {
    expect(playersInResult(tradeResult()).map((p) => p.id)).toEqual([1, 2, 3]);
  });
});

describe('balance meter maths', () => {
  it('clamps to whole percents', () => {
    expect(toBalancePercent(0.123)).toBe(12);
    expect(toBalancePercent(-4)).toBe(-100);
    expect(toBalancePercent(Number.NaN)).toBe(0);
  });

  it('describes the balance in words', () => {
    expect(balanceText(0)).toBe('Dead even');
    expect(balanceText(12)).toBe('You win by 12%');
    expect(balanceText(-8)).toBe('You lose by 8%');
  });
});
