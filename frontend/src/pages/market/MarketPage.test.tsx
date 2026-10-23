import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { BIJAN, CHASE, insight } from '../../test/fixtures';
import { json, mockFetch } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { MarketPage } from './MarketPage';

function respond(kind: 'buy' | 'sell', position: string | null) {
  if (position === 'TE') return { season: 2025, through_week: 7, scoring: 'ppr', results: [] };
  const who = kind === 'buy' ? BIJAN : CHASE;
  return { season: 2025, through_week: 7, scoring: 'ppr', results: [insight(who, { td_diff: null })] };
}

describe('MarketPage', () => {
  it('renders both lists with every reason and filters by position', async () => {
    const user = userEvent.setup();
    const api = mockFetch([
      META_ROUTE,
      { path: '/api/insights/buy-low', reply: ({ url }) => json(respond('buy', url.searchParams.get('position'))) },
      { path: '/api/insights/sell-high', reply: ({ url }) => json(respond('sell', url.searchParams.get('position'))) },
    ]);
    renderWithProviders(<MarketPage />);

    const buy = screen.getByRole('region', { name: 'Buy low' });
    expect(await within(buy).findByRole('link', { name: 'Bijan Robinson' })).toBeInTheDocument();
    expect(within(buy).getByText('Touchdowns are 2.1 below expectation.')).toBeInTheDocument();
    expect(within(buy).getByText((_, element) => element?.tagName === 'DD' && element.textContent === '11.2 vs 15.1')).toBeInTheDocument();
    expect(within(buy).getByText('9.8')).toBeInTheDocument();

    const sell = screen.getByRole('region', { name: 'Sell high' });
    expect(await within(sell).findByRole('link', { name: "Ja'Marr Chase" })).toBeInTheDocument();
    expect(api.callsTo('/api/insights/buy-low')[0].url.searchParams.get('limit')).toBe('15');

    await user.click(screen.getByRole('button', { name: 'TE' }));
    expect(await screen.findByText('No buy low candidates at TE.')).toBeInTheDocument();
    expect(await screen.findByText('No sell high candidates at TE.')).toBeInTheDocument();
    expect(api.callsTo('/api/insights/sell-high').at(-1)?.url.searchParams.get('position')).toBe('TE');
  });
});
