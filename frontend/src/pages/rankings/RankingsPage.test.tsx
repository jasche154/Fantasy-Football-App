import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { BIJAN, CHASE, ranked } from '../../test/fixtures';
import { json, mockFetch } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { RankingsPage } from './RankingsPage';

function rankingsResponse(position: string | null) {
  const rows = position === 'RB' ? [ranked(1, BIJAN)] : [ranked(1, BIJAN), ranked(2, CHASE)];
  return { season: 2025, through_week: 7, scoring: 'ppr', weeks_remaining: 10, results: rows };
}

describe('RankingsPage', () => {
  it('loads rankings with league settings and refetches when the position filter changes', async () => {
    const user = userEvent.setup();
    const api = mockFetch([
      META_ROUTE,
      { path: '/api/rankings', reply: ({ url }) => json(rankingsResponse(url.searchParams.get('position'))) },
    ]);
    renderWithProviders(<RankingsPage />);

    expect(await screen.findByRole('link', { name: "Ja'Marr Chase" })).toBeInTheDocument();
    expect(screen.getByText(/2025 · through week 7 · 10 weeks remaining · 12-team/)).toBeInTheDocument();
    const firstCall = api.callsTo('/api/rankings')[0];
    expect(firstCall.url.searchParams.get('scoring')).toBe('ppr');
    expect(firstCall.url.searchParams.get('limit')).toBe('100');
    expect(firstCall.url.searchParams.has('position')).toBe(false);

    await user.click(screen.getByRole('button', { name: 'RB' }));
    expect(screen.getByRole('button', { name: 'RB' })).toHaveAttribute('aria-pressed', 'true');
    await screen.findByText('1 player shown.');
    expect(screen.queryByRole('link', { name: "Ja'Marr Chase" })).not.toBeInTheDocument();
    expect(api.callsTo('/api/rankings').at(-1)?.url.searchParams.get('position')).toBe('RB');
  });

  it('shows an empty state and an error state with retry', async () => {
    const user = userEvent.setup();
    let attempts = 0;
    mockFetch([
      META_ROUTE,
      {
        path: '/api/rankings',
        reply: () => {
          attempts += 1;
          return attempts === 1
            ? json({ detail: 'Database unavailable' }, 503)
            : json({ season: 2025, through_week: null, scoring: 'ppr', weeks_remaining: 17, results: [] });
        },
      },
    ]);
    renderWithProviders(<RankingsPage />);
    expect(await screen.findByText('Database unavailable')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('No rankings yet.')).toBeInTheDocument();
  });
});
