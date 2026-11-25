import { screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { json, mockFetch } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { GAME_ID, seasons, summary } from './fixtures';
import { PlayPage, ReplayPage } from './ReplayPage';

function mockApi() {
  return mockFetch([
    META_ROUTE,
    { path: '/api/replay/games', reply: json({ results: [] }) },
    { path: '/api/replay/seasons', reply: json(seasons()) },
    { path: `/api/replay/games/${GAME_ID}`, reply: json(summary()) },
  ]);
}

async function currentTab(): Promise<string | null> {
  const tabs = within(await screen.findByRole('navigation', { name: 'League sections' })).getAllByRole('link');
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

  it('sends an old league link on to the game screen, keeping its query and leaving no history entry', async () => {
    mockApi();
    const before = window.history.length;
    renderWithProviders(<ReplayPage query={{ game: '3', view: 'trades', with: '2' }} />, { hash: '#/replay?game=3&view=trades&with=2' });
    await waitFor(() => expect(window.location.hash).toBe('#/play?game=3&view=trades&with=2'));
    expect(window.history.length).toBe(before);
  });
});

describe('PlayPage', () => {
  it('opens the league and the tab named in the URL', async () => {
    mockApi();
    renderWithProviders(<PlayPage query={{ game: String(GAME_ID), view: 'trades' }} />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Sunday Replay' })).toBeInTheDocument();
    expect(await currentTab()).toBe('Trades');
    expect(document.title).toBe('Sunday Replay · Gridiron Edge');
  });

  it('opens the Matchup tab on the game named by week and team', async () => {
    const api = mockApi();
    renderWithProviders(<PlayPage query={{ game: String(GAME_ID), view: 'matchup', week: '3', team: '2' }} />);
    expect(await currentTab()).toBe('Matchup');
    await waitFor(() => expect(api.callsTo(`/api/replay/games/${GAME_ID}/matchup`)[0]?.url.search).toBe('?week=3&team_id=2'));
  });

  it('falls back to My Team for an unknown tab and to the lobby for a bad game id', async () => {
    mockApi();
    const { unmount } = renderWithProviders(<PlayPage query={{ game: String(GAME_ID), view: 'nope' }} />);
    expect(await currentTab()).toBe('My Team');
    unmount();
    renderWithProviders(<PlayPage query={{ game: 'abc' }} />, { hash: '#/play?game=abc' });
    await waitFor(() => expect(window.location.hash).toBe('#/replay'));
  });
});
