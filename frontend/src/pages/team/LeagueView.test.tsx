import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { LeagueOverview, MyLeague, PowerRankingRow } from '../../api/types';
import { json, mockFetch } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { LeagueView } from './LeagueView';

const LEAGUE: MyLeague = { id: 1, key: '470.l.1', name: 'Family League', season: 2026, team: 'My Squad',
  scoring: 'half', teams: 12, starters: { QB: 1, RB: 2, WR: 2, TE: 1, FLEX: 1 }, week: 8 };

function team(overrides: Partial<PowerRankingRow>): PowerRankingRow {
  return { power_rank: 1, team: 'Rival', team_key: 'r', is_users_team: false, record: '6-1', standing: 1,
    points_for: 876.2, points_against: 600, strength: 109.8, remaining_opponent_strength: 91.6, playoff_odds: 0.9,
    expected_wins: 10, tendencies: null, ...overrides };
}

const OVERVIEW: LeagueOverview = {
  league: 'Family League', playoff_teams: 6, regular_season_weeks_left: 7, simulations: 10000,
  teams: [
    team({ all_play: '52-33', all_play_expected_wins: 4.7, luck: 1.3,
      tendencies: { moves: 14, trades: 4, label: 'Active trader', trades_for: [], trades_away: [], picks_up: ['RB'],
        tags: [{ tag: 'Active trader', evidence: '4 trades' }, { tag: 'RB hunter', evidence: '62% of 8 adds and trades were RB' }] } }),
    team({ power_rank: 2, team: 'My Squad', team_key: 'm', is_users_team: true, luck: -0.8, all_play: '40-45' }),
    team({ power_rank: 3, team: 'New Team', team_key: 'n' }),
  ],
  past_trades: [
    { date: '2026-10-14', first_week: 7, weeks: [7, 8, 9], graded: true, winner: 'Rival', winner_key: 'r', margin: 23.4,
      unmatched: ['Some Kicker'], sides: [
        { team_key: 'r', team: 'Rival', received: [{ name: 'Bijan Robinson', points: 60.1 }], sent: [],
          received_points: 60.1, sent_points: 36.7, net: 23.4 },
        { team_key: 'm', team: 'My Squad', received: [{ name: 'Ja\'Marr Chase', points: 36.7 }], sent: [],
          received_points: 36.7, sent_points: 60.1, net: -23.4 }] },
    { date: '2026-10-30', first_week: null, weeks: [], graded: false, winner: null, winner_key: null, margin: null,
      unmatched: [], sides: [] },
    { date: '2026-09-20', first_week: 3, weeks: [3, 4, 6], graded: true, winner: 'My Squad', winner_key: 'm',
      margin: 12, unmatched: [], sides: [
        { team_key: 'm', team: 'My Squad', received: [{ name: 'Puka Nacua', points: 50 }], sent: [],
          received_points: 50, sent_points: 38, net: 12 }] },
  ],
};

const LUCK_COLUMN = 8;           // #, Team, Record, PF, Strength, Opp. left, Playoff odds, Proj. wins, Luck

function luckCell(row: HTMLElement): HTMLElement {
  return within(row).getAllByRole('cell')[LUCK_COLUMN];
}

function luckTitle(row: HTMLElement): string | null {
  return luckCell(row).querySelector('[title]')?.getAttribute('title') ?? null;
}

describe('LeagueView', () => {
  it('shows luck, the all-play record and tag chips with their evidence', async () => {
    mockFetch([META_ROUTE, { path: '/api/my-team/1/league', reply: json(OVERVIEW) }]);
    renderWithProviders(<LeagueView league={LEAGUE} />);
    const table = await screen.findByRole('table', { name: 'Power rankings for Family League' });
    const rival = within(table).getByText('Rival').closest('tr') as HTMLElement;
    expect(rival).toHaveTextContent('Luck +1.3');
    expect(rival).toHaveTextContent('All-play 52-33');
    const tags = within(rival).getByRole('list', { name: 'Manager tags' });
    const chips = within(tags).getAllByRole('listitem');
    expect(chips.map((chip) => chip.textContent)).toEqual(
      ['Active trader (4 trades)', 'RB hunter (62% of 8 adds and trades were RB)']);
    expect(luckTitle(rival)).toBe('Actual wins minus 4.7 all-play expected wins');
    const mine = within(table).getByText('My Squad').closest('tr') as HTMLElement;
    expect(luckCell(mine)).toHaveTextContent('Luck −0.8');
    // No expected-wins number: the tooltip leaves it out instead of printing a dash.
    expect(luckTitle(mine)).toBe('Actual wins minus all-play expected wins');
    expect(within(mine).queryByRole('list', { name: 'Manager tags' })).toBeNull();
    expect(luckCell(within(table).getByText('New Team').closest('tr') as HTMLElement)).toHaveTextContent(/^—$/);
  });

  it('lists past trades with each side\'s points since and the winner', async () => {
    mockFetch([META_ROUTE, { path: '/api/my-team/1/league', reply: json(OVERVIEW) }]);
    renderWithProviders(<LeagueView league={LEAGUE} />);
    const past = await screen.findByRole('region', { name: 'Past trades' });
    const [graded, fresh, won] = within(past).getAllByRole('listitem').filter((li) => li.classList.contains('past-trade'));
    expect(graded).toHaveTextContent('2026-10-14 · weeks 7–9 · Rival won');
    // A rival's win is neutral; only the user's own win is shown in green.
    expect(within(graded).getByText('Rival won')).not.toHaveClass('status-badge--ok');
    expect(within(won).getByText('My Squad won')).toHaveClass('status-badge--ok');
    expect(won).toHaveTextContent('2026-09-20 · weeks 3, 4, 6 · My Squad won');
    expect(graded).toHaveTextContent('Rival got Bijan Robinson (60.1): +23.4 pts since the trade');
    expect(graded).toHaveTextContent('My Squad got Ja\'Marr Chase (36.7): −23.4 pts since the trade');
    expect(graded).toHaveTextContent('Not counted (no stats match): Some Kicker');
    expect(fresh).toHaveTextContent('2026-10-30 · no weeks played since');
    expect(fresh).not.toHaveTextContent('won');
  });

  it('hides the past trades section when there are none', async () => {
    mockFetch([META_ROUTE, { path: '/api/my-team/1/league', reply: json({ ...OVERVIEW, past_trades: [] }) }]);
    renderWithProviders(<LeagueView league={LEAGUE} />);
    await screen.findByRole('table', { name: 'Power rankings for Family League' });
    expect(screen.queryByRole('region', { name: 'Past trades' })).toBeNull();
  });
});
