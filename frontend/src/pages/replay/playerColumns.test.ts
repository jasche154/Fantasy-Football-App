import { describe, expect, it } from 'vitest';
import { MAHOMES, MOSTERT, statLine, wireRow } from './fixtures';
import { boxLine } from './fixturesPlayers';
import { boxColumns, boxText, DEFAULT_SORT, sortDirection, statColumns, visibleSort } from './playerColumns';

const labels = (span: Parameters<typeof statColumns>[0], tab: Parameters<typeof statColumns>[2]) =>
  statColumns(span, span === 'week' ? 5 : null, tab).map((column) => column.label);

describe('playerColumns', () => {
  it('picks the span columns, then the box columns for the position tab', () => {
    expect(labels('season', 'ALL')).toEqual(['Pts', 'PPG', 'GP', 'ROS', 'Car', 'RuYd', 'Tgt', 'Rec', 'ReYd']);
    expect(labels('last4', 'QB')).toEqual(['Pts/G', 'Snap %', 'PaYd', 'PaTD', 'INT', 'RuYd']);
    expect(labels('week', 'WR')).toEqual(['Proj', 'Pts', 'Tgt', 'Rec', 'ReYd', 'ReTD']);
    expect(labels('week', 'TE')).toEqual(labels('week', 'WR'));
    expect(labels('season', 'FLEX')).toEqual(labels('season', 'RB'));
    expect(labels('season', 'K')).toEqual(['Pts', 'PPG', 'GP', 'ROS']);
    expect(boxColumns('DST')).toEqual([]);
  });

  it('sorts on the server keys, and leaves GP and Snap % unsortable', () => {
    expect(statColumns('season', null, 'RB').map((column) => column.sort)).toEqual(
      ['line_points', 'ppg', null, 'ros_ppg', 'carries', 'rush_yds', 'targets', 'rec', 'rec_yds']);
    expect(statColumns('week', 3, 'K').map((column) => column.sort)).toEqual(['line_projection', 'line_points']);
    expect(statColumns('last4', null, 'K')[1].sort).toBeNull();
  });

  it('shows a dash for an unplayed week and averages with one decimal', () => {
    const [proj, pts] = statColumns('week', 5, 'RB');
    const unplayed = wireRow(MOSTERT, { line: statLine({ span: 'week', week: 5, points: null, projection: 14.2 }) });
    expect([proj.text(unplayed), pts.text(unplayed)]).toEqual(['14.20', '—']);
    expect(boxText(boxLine({ rush_yds: 64.25 }), 'rush_yds', true)).toBe('64.3');
    expect(boxText(boxLine(), 'rush_yds')).toBe('72');
    expect(boxText(null, 'rush_yds')).toBe('—');
    const games = statColumns('season', null, 'QB').find((column) => column.id === 'ppg')!;
    expect(games.text(wireRow(MAHOMES, { games: 0 }))).toBe('—');
  });

  it('starts names and ADP low to high, everything else high to low', () => {
    expect(['name', 'adp', 'value', 'rush_yds'].map((key) => sortDirection(key as never))).toEqual(['asc', 'asc', 'desc', 'desc']);
  });

  it('falls back to Value when the sorted column is no longer shown', () => {
    const byCarries = { key: 'carries' as const, direction: 'asc' as const };
    expect(visibleSort(byCarries, statColumns('season', null, 'RB'))).toBe(byCarries);
    expect(visibleSort(byCarries, statColumns('season', null, 'WR'))).toBe(DEFAULT_SORT);
    expect(visibleSort({ key: 'name', direction: 'desc' }, [])).toEqual({ key: 'name', direction: 'desc' });
  });
});
