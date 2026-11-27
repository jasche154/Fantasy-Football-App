import { describe, expect, it } from 'vitest';
import { MAHOMES, MOSTERT, statLine, wireRow } from './fixtures';
import { boxLine } from './fixturesPlayers';
import { boxColumns, boxText, DEFAULT_SORT, passingText, sortDirection, statColumns, visibleSort } from './playerColumns';

const labels = (span: Parameters<typeof statColumns>[0], tab: Parameters<typeof statColumns>[2]) =>
  statColumns(span, span === 'week' ? 5 : null, tab).map((column) => column.label);

describe('playerColumns', () => {
  it('picks the span columns, then the box columns for the position tab', () => {
    expect(labels('season', 'ALL')).toEqual(['Pts', 'PPG', 'GP', 'ROS', 'Car', 'RuYd', 'RuTD', 'Tgt', 'Rec', 'ReYd', 'ReTD']);
    expect(labels('last4', 'QB')).toEqual(['Pts/G', 'Snap % (last 3)', 'Cmp', 'Att', 'PaYd', 'PaTD', 'INT', 'RuYd', 'RuTD']);
    expect(labels('week', 'WR')).toEqual(['Proj', 'Pts', 'Tgt', 'Rec', 'ReYd', 'ReTD']);
    expect(labels('week', 'TE')).toEqual(labels('week', 'WR'));
    expect(labels('season', 'FLEX')).toEqual(labels('season', 'RB'));
    expect(labels('season', 'K')).toEqual(['Pts', 'PPG', 'GP', 'ROS']);
    expect(boxColumns('DST')).toEqual([]);
  });

  it('sorts on the server keys, and leaves GP and Snap % unsortable', () => {
    expect(statColumns('season', null, 'RB').map((column) => column.sort)).toEqual(
      ['line_points', 'ppg', null, 'ros_ppg', 'carries', 'rush_yds', 'rush_td', 'targets', 'rec', 'rec_yds', 'rec_td']);
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
    expect(boxText(boxLine({ pass_att: null }), 'pass_att')).toBe('—');       // a saved league never recorded it
    expect([passingText(boxLine({ pass_cmp: 22, pass_att: 34 })), passingText(boxLine({ pass_cmp: null, pass_att: null })), passingText(null)])
      .toEqual(['22/34', '—', '—']);
    const games = statColumns('season', null, 'QB').find((column) => column.id === 'ppg')!;
    expect(games.text(wireRow(MAHOMES, { games: 0 }))).toBe('—');
  });

  it('starts names and ADP low to high, everything else high to low', () => {
    expect(['name', 'adp', 'value', 'rush_yds'].map((key) => sortDirection(key as never))).toEqual(['asc', 'asc', 'desc', 'desc']);
  });

  it('shows usage and efficiency per position in the Advanced view, each sorted on the server', () => {
    const advanced = (tab: Parameters<typeof statColumns>[2]) => statColumns('season', null, tab, 'advanced');
    expect(advanced('RB').map((column) => column.label)).toEqual(['GP', 'Snap%', 'Tch/G', 'Car/G', 'YPC', 'Tgt%', 'Tgt/G', 'Catch%', 'Y/Rec', 'TD%', 'TD']);
    expect(advanced('WR').map((column) => column.label)).toEqual(['GP', 'Snap%', 'Tgt%', 'Tgt/G', 'Catch%', 'Y/Tgt', 'Y/Rec', 'TD%', 'TD']);
    expect(advanced('QB').map((column) => column.label)).toEqual(['GP', 'Snap%', 'PaYd/G', 'Cmp%', 'TD:INT', 'Car/G', 'YPC', 'TD']);
    expect(advanced('ALL')).toHaveLength(15);
    expect(advanced('K').map((column) => column.label)).toEqual(['GP']);
    expect(advanced('RB').map((column) => column.sort)).toEqual([null, 'adv_snap_pct', 'adv_touches_pg', 'adv_carries_pg', 'adv_ypc',
      'adv_target_share', 'adv_targets_pg', 'adv_catch_rate', 'adv_ypr', 'adv_td_rate', 'adv_total_td']);
  });

  it('formats advanced numbers: percents, one decimal, TD:INT with two, and a dash when missing', () => {
    const [, snap, , , ypc] = statColumns('season', null, 'RB', 'advanced');
    const tdInt = statColumns('season', null, 'QB', 'advanced').find((column) => column.id === 'td_int')!;
    const td = statColumns('last4', null, 'RB', 'advanced').find((column) => column.id === 'total_td')!;
    const adv = { snap_pct: 62.44, ypc: 4.25, td_int: 2.5, total_td: 0.75 };
    const row = wireRow(MOSTERT, { line: statLine({ adv: adv as never }) });
    expect([snap.text(row), ypc.text(row), tdInt.text(row), td.text(row)]).toEqual(['62.4%', '4.3', '2.50', '0.8']);
    expect(snap.text(wireRow(MOSTERT, { line: statLine({ adv: null }) }))).toBe('—');
  });

  it('falls back to Value when the sorted column is no longer shown', () => {
    const byCarries = { key: 'carries' as const, direction: 'asc' as const };
    expect(visibleSort(byCarries, statColumns('season', null, 'RB'))).toBe(byCarries);
    expect(visibleSort(byCarries, statColumns('season', null, 'WR'))).toBe(DEFAULT_SORT);
    expect(visibleSort({ key: 'name', direction: 'desc' }, [])).toEqual({ key: 'name', direction: 'desc' });
    const bySnaps = { key: 'adv_snap_pct' as const, direction: 'desc' as const };
    expect(visibleSort(bySnaps, statColumns('season', null, 'RB', 'advanced'))).toBe(bySnaps);
    expect(visibleSort(bySnaps, statColumns('season', null, 'RB'))).toBe(DEFAULT_SORT);
  });
});
