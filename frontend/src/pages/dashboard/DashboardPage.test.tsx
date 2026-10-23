import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ALLEN, BIJAN, CHASE, insight, meta, player } from '../../test/fixtures';
import { json, mockFetch, type MockRoute } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { DashboardPage, freshnessLabel } from './DashboardPage';
import { SLEEPER_DOWN_MESSAGE } from './TrendingCard';

const BUY_ROWS = [1, 2, 3, 4, 5, 6].map((id) => insight(player(id, `Buy Guy ${id}`)));

function insightRoutes(): MockRoute[] {
  return [
    { path: '/api/insights/buy-low', reply: json({ season: 2025, through_week: 7, scoring: 'ppr', results: BUY_ROWS }) },
    {
      path: '/api/insights/sell-high',
      reply: json({
        season: 2025,
        through_week: 7,
        scoring: 'ppr',
        results: [insight(CHASE, { fpoe_per_game: 4.2, td_diff: 3, reasons: ['Scoring 4.2 more PPR pts/game than usage predicts.'] })],
      }),
    },
  ];
}

describe('DashboardPage', () => {
  it('shows freshness, top 5 buy-low with the first reason, sell-high, and quick links', async () => {
    mockFetch([META_ROUTE, ...insightRoutes(), { path: '/api/insights/trending', reply: json({ results: [] }) }]);
    renderWithProviders(<DashboardPage />);

    expect(await screen.findByText('2025 season · stats through week 7')).toBeInTheDocument();
    const buyCard = screen.getByRole('region', { name: 'Buy low' });
    expect(await within(buyCard).findByRole('link', { name: 'Buy Guy 1' })).toBeInTheDocument();
    expect(within(buyCard).getAllByRole('link', { name: /Buy Guy/ })).toHaveLength(5);
    expect(within(buyCard).getAllByText(/fewer PPR pts\/game/)).toHaveLength(5);
    expect(within(buyCard).queryByText('Touchdowns are 2.1 below expectation.')).not.toBeInTheDocument();

    const sellCard = screen.getByRole('region', { name: 'Sell high' });
    expect(await within(sellCard).findByText('+4.2')).toBeInTheDocument();
    expect(within(sellCard).getByText('+3.0')).toBeInTheDocument();

    expect(screen.getByRole('link', { name: /Trade calculator/ })).toHaveAttribute('href', '#/trade');
    expect(await screen.findByText('No trending adds reported yet.')).toBeInTheDocument();
  });

  it('handles a 502 from Sleeper gracefully', async () => {
    mockFetch([META_ROUTE, ...insightRoutes(), { path: '/api/insights/trending', reply: json({ detail: 'Sleeper unreachable' }, 502) }]);
    renderWithProviders(<DashboardPage />);
    const trending = screen.getByRole('region', { name: 'Trending adds' });
    expect(await within(trending).findByText(SLEEPER_DOWN_MESSAGE)).toBeInTheDocument();
    expect(within(trending).getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('lists trending adds, including unmatched Sleeper players', async () => {
    mockFetch([
      META_ROUTE,
      ...insightRoutes(),
      {
        path: '/api/insights/trending',
        reply: json({
          results: [
            { player: BIJAN, sleeper_id: '9509', count: 41234 },
            { player: null, sleeper_id: '1234', count: 900 },
            { player: ALLEN, sleeper_id: '4984', count: 0 },
          ],
        }),
      },
    ]);
    renderWithProviders(<DashboardPage />);
    const trending = screen.getByRole('region', { name: 'Trending adds' });
    expect(await within(trending).findByText('41,234')).toBeInTheDocument();
    expect(within(trending).getByText('Unmatched Sleeper player #1234')).toBeInTheDocument();
  });

  it('reports meta failures and empty insight lists', async () => {
    mockFetch([
      { path: '/api/meta', reply: json({ detail: 'DB locked' }, 500) },
      { path: '/api/insights/buy-low', reply: json({ season: 2025, through_week: 7, scoring: 'ppr', results: [] }) },
      { path: '/api/insights/sell-high', reply: json({ detail: 'boom' }, 500) },
      { path: '/api/insights/trending', reply: json({ results: [] }) },
    ]);
    renderWithProviders(<DashboardPage />);
    expect(await screen.findByText('Data freshness unavailable: DB locked')).toBeInTheDocument();
    expect(await screen.findByText('No buy-low candidates right now.')).toBeInTheDocument();
    expect(await screen.findByText('boom')).toBeInTheDocument();
  });
});

describe('freshnessLabel', () => {
  it('handles a season with no games yet', () => {
    expect(freshnessLabel(meta({ latest_week: null }))).toBe('2025 season · no games played yet');
  });
});
