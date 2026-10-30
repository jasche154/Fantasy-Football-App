import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { MyLeague, MyTeamResponse, PlayerSummary, WeeklyPlayer } from '../../api/types';
import { BIJAN, CHASE } from '../../test/fixtures';
import { json, mockFetch } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { MyTeamPage } from './MyTeamPage';

const LEAGUE: MyLeague = { id: 1, key: '470.l.1', name: 'Family League', season: 2026, team: 'My Squad',
  scoring: 'half', teams: 12, starters: { QB: 1, RB: 2, WR: 2, TE: 1, FLEX: 1 }, week: 8 };

function week(player: PlayerSummary, projection: number): WeeklyPlayer {
  return { rank: 1, pos_rank: 'RB1', player, projection, if_active: projection, play_prob: 1, status: null,
    floor: projection / 2, ceiling: projection * 2, boost_points: 0, pct_rostered: 99, scenarios: [], flags: [],
    matchup: { opponent: 'SF', home: true, sos_factor: 1, sos_rank: 10, implied_total: 24, team_ppg: 23, game_final: false },
    sources: {}, expert_ranks: {}, actual: null };
}

const TEAM: MyTeamResponse = {
  league: LEAGUE, week: 8,
  roster: [
    { slot: 'WR', starting: true, in_best_lineup: false, player: CHASE, week: week(CHASE, 6.4), ros_ppg: 9, trade_value: 20, pos_rank: 'WR40' },
    { slot: 'BN', starting: false, in_best_lineup: true, player: BIJAN, week: week(BIJAN, 19.9), ros_ppg: 18, trade_value: 120, pos_rank: 'RB2' },
  ],
  lineup: { current_total: 70.1, best_total: 83.6,
    changes: [{ start: BIJAN, start_projection: 19.9, bench: CHASE, bench_projection: 6.4, slot: 'FLEX', gain: 13.5 }] },
};

describe('MyTeamPage', () => {
  it('shows the roster, the best-lineup advice and start/sit tags', async () => {
    mockFetch([META_ROUTE, { path: '/api/my-team/leagues', reply: json({ leagues: [LEAGUE] }) },
      { path: '/api/my-team/1', reply: json(TEAM) }]);
    renderWithProviders(<MyTeamPage />);
    expect(await screen.findByRole('heading', { level: 1, name: 'My Squad' })).toBeInTheDocument();
    const advice = await screen.findByRole('region', { name: 'Lineup advice' });
    expect(advice).toHaveTextContent('Best lineup projects 83.6, against 70.1');
    expect(within(advice).getByText('+13.5')).toBeInTheDocument();
    expect(within(screen.getByRole('table', { name: 'Starters' })).getByText('Sit')).toBeInTheDocument();
    expect(within(screen.getByRole('table', { name: 'Bench and injured reserve' })).getByText('Start')).toBeInTheDocument();
  });

  it('lists league trades by team with their needs', async () => {
    const offer = { give: [CHASE], get: BIJAN, get_ros_ppg: 18, fills_their_need: ['WR'],
      evaluation: { verdict: 'fair' } };
    mockFetch([META_ROUTE, { path: '/api/my-team/leagues', reply: json({ leagues: [LEAGUE] }) },
      { path: '/api/my-team/1', reply: json(TEAM) },
      { path: '/api/my-team/1/trades', reply: json({ league: 'Family League', perspective: 'give', teams: [
        { team: 'Rival', owner: 'Sam', needs: ['WR'], offers: [offer] }] }) }]);
    const user = userEvent.setup();
    renderWithProviders(<MyTeamPage />);
    await screen.findByRole('region', { name: 'Lineup advice' });
    await user.click(screen.getByRole('button', { name: 'Trades' }));
    const list = await screen.findByRole('list', { name: 'Trade ideas in Family League' });
    expect(list).toHaveTextContent('Needs WR');
    expect(list).toHaveTextContent("Give Ja'Marr Chase for Bijan Robinson (RB)");
    expect(within(list).getByText('Fills their WR need')).toBeInTheDocument();
  });

  it('shows the playoff weeks, byes and the best lineup each week', async () => {
    const cell = (week: number, opponent: string | null, projection: number) =>
      ({ week, opponent, bye: opponent === null, factor: opponent ? 1.1 : null, projection });
    mockFetch([META_ROUTE, { path: '/api/my-team/leagues', reply: json({ leagues: [LEAGUE] }) },
      { path: '/api/my-team/1', reply: json(TEAM) },
      { path: '/api/my-team/1/playoffs', reply: json({ league: 'Family League', team: 'My Squad', weeks: [16, 17, 18],
        source: 'league', weakest_week: 18,
        players: [{ player: BIJAN, slot: 'RB', ros_ppg: 18, weeks: [cell(16, 'NYG', 20.1), cell(17, null, 0), cell(18, 'CHI', 17)],
          playoff_total: 37.1 }],
        lineups: { 16: { projected: 95, starters: [1], empty_slots: [] }, 17: { projected: 80, starters: [], empty_slots: ['RB'] },
          18: { projected: 78, starters: [1], empty_slots: [] } } }) }]);
    const user = userEvent.setup();
    renderWithProviders(<MyTeamPage />);
    await screen.findByRole('region', { name: 'Lineup advice' });
    await user.click(screen.getByRole('button', { name: 'Playoffs' }));
    const outlook = await screen.findByRole('region', { name: 'Playoff outlook' });
    expect(outlook).toHaveTextContent("Playoff weeks 16, 17, 18 (your league's schedule)");
    const row = within(outlook).getByRole('link', { name: 'Bijan Robinson' }).closest('tr') as HTMLElement;
    expect(row).toHaveTextContent('20.1vs NYG');
    expect(row).toHaveTextContent('BYE');
    expect(outlook).toHaveTextContent('empty: RB');
    expect(outlook).toHaveTextContent('Your weakest playoff week is week 18');
  });

  it('explains how to import a league when none is connected', async () => {
    mockFetch([META_ROUTE, { path: '/api/my-team/leagues', reply: json({ leagues: [] }) }]);
    renderWithProviders(<MyTeamPage />);
    expect(await screen.findByText('No Yahoo league imported yet.')).toBeInTheDocument();
  });
});
