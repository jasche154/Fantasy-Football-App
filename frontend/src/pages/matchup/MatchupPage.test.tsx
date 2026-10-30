import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { MatchupResponse } from '../../api/types';
import { json, mockFetch } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { MatchupPage } from './MatchupPage';

const LEAGUE = { id: 1, key: 'k', name: 'Family League', season: 2026, team: 'My Squad', scoring: 'half', teams: 12,
  starters: null, week: 8 };
const starter = (name: string, projection: number) => ({ slot: 'RB', player_id: projection, name, position: 'RB', team: 'DET',
  opponent: 'MIN', projection, floor: projection / 2, ceiling: projection * 2, actual: null, status: null });
const MATCHUP: MatchupResponse = {
  league: { id: 1, name: 'Family League' }, week: 8,
  me: { team: 'My Squad', starters: [starter('Blake Corum', 9.3)], projected: 100.4, yahoo_points: 8.6, yahoo_projected: 102.6 },
  opponent: { team: 'Rival', starters: [starter('Rival Back', 12)], projected: 107.4, yahoo_points: 0, yahoo_projected: 107 },
  win_probability: 0.408, yahoo_win_probability: 0.32,
  strategies: { expected: { projected: 111, win_probability: 0.545 }, upside: { projected: 110, win_probability: 0.53 },
    safe: { projected: 109, win_probability: 0.523 } },
  recommended: 'expected', changes: { start: ['Jahmyr Gibbs'], sit: ['Blake Corum'] }, simulations: 20000,
  updated: { mode: 'expected', starters: [{ ...starter('Jahmyr Gibbs', 19.7), slot: 'FLEX' }], projected: 114, win_probability: 0.579 },
  bench: [{ ...starter('Blake Corum', 9.1), slot: 'W/R/T' }, { ...starter('Kyle Monangai', 3.5), slot: 'BN' }],
};

describe('MatchupPage', () => {
  it('shows both lineups, the win probability and the recommended strategy', async () => {
    mockFetch([META_ROUTE, { path: '/api/my-team/leagues', reply: json({ leagues: [LEAGUE] }) },
      { path: '/api/my-team/1/matchup', reply: json(MATCHUP) }]);
    renderWithProviders(<MatchupPage />);
    const strategy = await screen.findByRole('region', { name: 'Lineup strategy' });
    expect(strategy).toHaveTextContent('Your current lineup wins 41%');
    expect(strategy).toHaveTextContent('Yahoo says 32%');
    expect(within(strategy).getByText('Recommended').closest('li')).toHaveTextContent('Most points lineup: 111.0 projected, wins 55%');
    expect(strategy).toHaveTextContent('start Jahmyr Gibbs; sit Blake Corum');
    expect(screen.getByRole('region', { name: 'My Squad (you)' })).toHaveTextContent('Blake Corum');
    expect(screen.getByRole('region', { name: 'Rival' })).toHaveTextContent('Rival Back');
    const updated = screen.getByRole('region', { name: /^Updated lineup/ });
    expect(updated).toHaveTextContent('Updated lineup (Most points) · wins 58% 114.0');
    expect(updated).toHaveTextContent('Jahmyr Gibbs');
    const bench = screen.getByRole('region', { name: 'Bench' });
    expect(bench).toHaveTextContent('Blake Corum');
    expect(bench).toHaveTextContent('Kyle Monangai');
  });

  it('explains how to connect when no league is imported', async () => {
    mockFetch([META_ROUTE, { path: '/api/my-team/leagues', reply: json({ leagues: [] }) }]);
    renderWithProviders(<MatchupPage />);
    expect(await screen.findByText('No Yahoo league imported yet.')).toBeInTheDocument();
  });
});
