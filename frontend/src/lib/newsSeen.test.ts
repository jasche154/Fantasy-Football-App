import { describe, expect, it } from 'vitest';
import { markSeen, newsState, NEW_NEWS_HOURS } from './newsSeen';

const NOW = Date.parse('2026-10-30T12:00:00Z');
const hoursAgo = (h: number) => new Date(NOW - h * 3600 * 1000).toISOString();

describe('newsState', () => {
  it('is new for recent news you have not opened, seen otherwise, and null without news', () => {
    expect(newsState(hoursAgo(3), {}, 1, NOW)).toBe('new');
    expect(newsState(hoursAgo(NEW_NEWS_HOURS + 1), {}, 1, NOW)).toBe('seen');
    expect(newsState(hoursAgo(3), { 1: hoursAgo(3) }, 1, NOW)).toBe('seen');
    expect(newsState(hoursAgo(1), { 1: hoursAgo(3) }, 1, NOW)).toBe('new');        // newer than what you read
    expect(newsState(undefined, {}, 1, NOW)).toBeNull();
    expect(newsState('garbage', {}, 1, NOW)).toBe('seen');
  });

  it('remembers what you opened without changing the old record', () => {
    const before = { 2: 'x' };
    const after = markSeen(before, 1, hoursAgo(1));
    expect(after).toEqual({ 2: 'x', 1: hoursAgo(1) });
    expect(before).toEqual({ 2: 'x' });
  });
});
