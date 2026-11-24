import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { json, mockFetch, type MockReply, type MockRoute } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { actionResult, draftSummary, gameListing, seasons } from './fixtures';
import { ReplayHome } from './ReplayHome';

const GAMES_PATH = '/api/replay/games';
const SEASONS_ROUTE: MockRoute = { path: '/api/replay/seasons', reply: json(seasons()) };
const SAVED = [
  gameListing(),
  gameListing({ id: 3, name: 'Class of 2019', season: 2019, phase: 'complete', week: 16, teams: 10, scoring: 'ppr', champion: 'Hail Marys' }),
  gameListing({ id: 2, name: 'Fresh Start', phase: 'draft', week: 0 }),
];

function renderHome(routes: MockRoute[]) {
  const api = mockFetch([META_ROUTE, SEASONS_ROUTE, ...routes]);
  renderWithProviders(<ReplayHome />, { hash: '#/replay' });
  return { api, user: userEvent.setup() };
}

describe('ReplayHome', () => {
  it('lists saved leagues with where each one stands and a way back in', async () => {
    renderHome([{ path: GAMES_PATH, reply: json({ results: SAVED }) }]);
    const table = await screen.findByRole('table', { name: 'Your saved leagues' });
    const [current, finished, drafting] = within(table).getAllByRole('row').slice(1);
    expect(current).toHaveTextContent('Sunday Replay12 teams · Half PPR2023Week 5—');
    expect(finished).toHaveTextContent('Class of 201910 teams · PPR2019Season completeHail Marys');
    expect(drafting).toHaveTextContent('Drafting');
    expect(within(current).getByRole('link', { name: 'Play Sunday Replay' })).toHaveAttribute('href', '#/play?game=7');
    expect(screen.getByRole('heading', { level: 2, name: 'New league' })).toBeInTheDocument();
  });

  it('deletes a league only after a second, confirming press', async () => {
    let saved = SAVED;
    const { api, user } = renderHome([
      { path: GAMES_PATH, reply: () => json({ results: saved }) },
      { method: 'POST', path: `${GAMES_PATH}/3/delete`, reply: () => { saved = SAVED.filter((game) => game.id !== 3); return json({ deleted: 3 }); } },
    ]);
    window.localStorage.setItem('replay-chat-seen:3', '140');      // its chat count must not reach a league that reuses id 3
    const row = (await screen.findByRole('rowheader', { name: /Class of 2019/ })).closest('tr') as HTMLElement;

    // The lobby's own region (the new-league form below has its own status regions, mounted empty too).
    const region = screen.getAllByRole('status').find((status) => status.classList.contains('replay-notice'));
    expect(region).toBeEmptyDOMElement();
    await user.click(within(row).getByRole('button', { name: 'Delete Class of 2019' }));
    await user.click(within(row).getByRole('button', { name: 'Cancel' }));
    expect(api.calls.filter((call) => call.method === 'POST')).toHaveLength(0);

    await user.click(within(row).getByRole('button', { name: 'Delete Class of 2019' }));
    await user.click(within(row).getByRole('button', { name: 'Yes, delete Class of 2019' }));
    expect(await screen.findByRole('rowheader', { name: /Fresh Start/ })).toBeInTheDocument();
    await screen.findByRole('table');
    expect(api.callsTo(`${GAMES_PATH}/3/delete`, 'POST')).toHaveLength(1);
    await expect.poll(() => screen.queryByRole('rowheader', { name: /Class of 2019/ })).toBeNull();
    const notice = screen.getByText('Deleted Class of 2019.');
    expect(notice).toBe(region);
    expect(notice).toHaveFocus();
    expect(window.localStorage.getItem('replay-chat-seen:3')).toBeNull();
  });

  it('keeps Delete off until the list without the deleted league has arrived', async () => {
    let reads = 0;
    let finish: (reply: MockReply) => void = () => undefined;
    const { user } = renderHome([
      { path: GAMES_PATH, reply: () => ((reads += 1) === 1 ? json({ results: SAVED }) : new Promise<MockReply>((resolve) => { finish = resolve; })) },
      { method: 'POST', path: `${GAMES_PATH}/3/delete`, reply: json({ deleted: 3 }) },
    ]);
    await user.click(await screen.findByRole('button', { name: 'Delete Class of 2019' }));
    await user.click(screen.getByRole('button', { name: 'Yes, delete Class of 2019' }));
    expect(await screen.findByText('Deleted Class of 2019.')).toBeInTheDocument();
    await waitFor(() => {
      expect(reads).toBe(2);
      expect(screen.getByRole('button', { name: 'Delete Class of 2019' })).toBeDisabled();       // still listed, but already gone
    });
    expect(screen.getByRole('button', { name: 'Delete Sunday Replay' })).toBeDisabled();
    finish(json({ results: SAVED.filter((game) => game.id !== 3) }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Delete Sunday Replay' })).toBeEnabled());
  });

  it('says why a league could not be deleted', async () => {
    const { user } = renderHome([
      { path: GAMES_PATH, reply: json({ results: SAVED }) },
      { method: 'POST', path: `${GAMES_PATH}/7/delete`, reply: json({ detail: 'There is no league 7.' }, 404) },
    ]);
    const row = (await screen.findByRole('rowheader', { name: /Sunday Replay/ })).closest('tr') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: 'Delete Sunday Replay' }));
    await user.click(within(row).getByRole('button', { name: 'Yes, delete Sunday Replay' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('There is no league 7.');
  });

  it('invites a first league when nothing is saved, and opens the new league once created', async () => {
    const { user } = renderHome([
      { path: GAMES_PATH, reply: json({ results: [] }) },
      { method: 'POST', path: GAMES_PATH, reply: json(actionResult(draftSummary({ id: 12 }))) },
    ]);
    expect(await screen.findByText('No leagues yet.')).toBeInTheDocument();
    await user.click(await screen.findByRole('button', { name: 'Create league and draft' }));
    await expect.poll(() => window.location.hash).toBe('#/play?game=12');
  });
});

describe('ReplayHome simulated leagues', () => {
  it('says which saved leagues are simulated, in words', async () => {
    renderHome([{ path: GAMES_PATH, reply: json({ results: [
      gameListing({ id: 8, name: 'Now League', season: 2026, kind: 'live' }),
      gameListing({ id: 9, name: 'Next Year', season: 2027, kind: 'future' }),
      gameListing(),
    ] }) }]);
    const [live, future, replay] = within(await screen.findByRole('table', { name: 'Your saved leagues' })).getAllByRole('row').slice(1);
    expect(within(live).getByText('Live 2026')).toHaveClass('replay-kind-chip');
    expect(within(future).getByText('Simulated')).toHaveClass('replay-kind-chip');
    expect(replay.querySelector('.replay-kind-chip')).toBeNull();
  });
});
