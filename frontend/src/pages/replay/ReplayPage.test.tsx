import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { json, mockFetch } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { GAME_ID, seasons, summary } from './fixtures';
import { ReplayPage } from './ReplayPage';

function mockApi() {
  return mockFetch([
    META_ROUTE,
    { path: '/api/replay/games', reply: json({ results: [] }) },
    { path: '/api/replay/seasons', reply: json(seasons()) },
    { path: `/api/replay/games/${GAME_ID}`, reply: json(summary()) },
  ]);
}

async function currentTab(): Promise<string | null> {
  const tabs = within(await screen.findByRole('navigation', { name: 'League sections' })).getAllByRole('button');
  return tabs.find((tab) => tab.getAttribute('aria-current') === 'page')?.textContent ?? null;
}

describe('ReplayPage', () => {
  it('shows the saved leagues and the new-league form without a game in the URL', async () => {
    const api = mockApi();
    renderWithProviders(<ReplayPage query={{}} />, { hash: '#/replay' });
    expect(screen.getByRole('heading', { level: 1, name: 'Replay League' })).toBeInTheDocument();
    expect(await screen.findByText('No leagues yet.')).toBeInTheDocument();
    expect(api.callsTo(`/api/replay/games/${GAME_ID}`)).toHaveLength(0);
  });

  it('opens the league and the tab named in the URL', async () => {
    mockApi();
    renderWithProviders(<ReplayPage query={{ game: String(GAME_ID), view: 'trades' }} />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Sunday Replay' })).toBeInTheDocument();
    expect(await currentTab()).toBe('Trades');
    expect(document.title).toBe('Sunday Replay · Gridiron Edge');
  });

  it('falls back to My Team for an unknown tab and to the home for a bad game id', async () => {
    mockApi();
    const { unmount } = renderWithProviders(<ReplayPage query={{ game: String(GAME_ID), view: 'nope' }} />);
    expect(await currentTab()).toBe('My Team');
    unmount();
    renderWithProviders(<ReplayPage query={{ game: 'abc' }} />);
    expect(screen.getByRole('heading', { level: 1, name: 'Replay League' })).toBeInTheDocument();
  });
});
