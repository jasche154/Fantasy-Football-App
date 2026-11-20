import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { ReplaySeasons } from '../../api/types';
import { json, mockFetch, type MockRoute } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { actionResult, draftSummary, readiness, seasons } from './fixtures';
import { SetupForm } from './SetupForm';

const SEASONS_PATH = '/api/replay/seasons';
const GAMES_PATH = '/api/replay/games';
const CREATED = draftSummary({ id: 12 });

function renderForm(routes: MockRoute[]) {
  const api = mockFetch([META_ROUTE, ...routes]);
  const onCreated = vi.fn();
  renderWithProviders(<SetupForm onCreated={onCreated} />);
  return { api, onCreated, user: userEvent.setup() };
}

describe('SetupForm', () => {
  it('creates a league with everything the user chose', async () => {
    const { api, onCreated, user } = renderForm([
      { path: SEASONS_PATH, reply: json(seasons()) },
      { method: 'POST', path: GAMES_PATH, reply: json(actionResult(CREATED)) },
    ]);
    await user.selectOptions(await screen.findByLabelText('Season'), '2022');
    await user.type(screen.getByLabelText('League name'), '  Throwback  ');
    await user.selectOptions(screen.getByLabelText('Teams'), '10');
    await user.selectOptions(screen.getByLabelText('Scoring'), 'ppr');
    await user.selectOptions(screen.getByLabelText('Draft slot'), '3');
    await user.click(screen.getByRole('radio', { name: /Free agency only/ }));
    await user.click(screen.getByRole('radio', { name: /Hard/ }));
    await user.click(screen.getByRole('button', { name: 'Create league and draft' }));

    await vi.waitFor(() => expect(onCreated).toHaveBeenCalledWith(CREATED));
    expect(api.callsTo(GAMES_PATH, 'POST')[0].body).toEqual({
      season: 2022, name: 'Throwback', teams: 10, scoring: 'ppr', draft_slot: 3, waiver_mode: 'none', difficulty: 'hard',
    });
  });

  it('defaults to the newest ready season and a random slot, and can send a random season', async () => {
    const { api, onCreated, user } = renderForm([
      { path: SEASONS_PATH, reply: json(seasons()) },
      { method: 'POST', path: GAMES_PATH, reply: json(actionResult(CREATED)) },
    ]);
    const season = await screen.findByLabelText('Season');
    expect(season).toHaveValue('2023');
    expect(within(season).getAllByRole('option').map((option) => option.textContent)).toEqual(['2022', '2023', 'Random season']);
    expect(screen.getByLabelText('Draft slot')).toHaveValue('random');

    await user.selectOptions(season, 'random');
    await user.click(screen.getByRole('button', { name: 'Create league and draft' }));
    await vi.waitFor(() => expect(onCreated).toHaveBeenCalled());
    // No name and no draft_slot: the server names the league and draws the slot.
    expect(api.callsTo(GAMES_PATH, 'POST')[0].body).toEqual({
      season: 'random', teams: 12, scoring: 'half', waiver_mode: 'wednesday', difficulty: 'normal',
    });
  });

  it('drops a draft slot the smaller league no longer has', async () => {
    const { user } = renderForm([{ path: SEASONS_PATH, reply: json(seasons()) }]);
    await user.selectOptions(await screen.findByLabelText('Draft slot'), '12');
    await user.selectOptions(screen.getByLabelText('Teams'), '8');
    expect(screen.getByLabelText('Draft slot')).toHaveValue('random');
    expect(within(screen.getByLabelText('Draft slot')).getAllByRole('option')).toHaveLength(9);
  });

  it('shows why a league could not be created', async () => {
    const { onCreated, user } = renderForm([
      { path: SEASONS_PATH, reply: json(seasons()) },
      { method: 'POST', path: GAMES_PATH, reply: json({ detail: 'The 2023 season is not prepared yet.' }, 503) },
    ]);
    await user.click(await screen.findByRole('button', { name: 'Create league and draft' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('The 2023 season is not prepared yet.');
    expect(onCreated).not.toHaveBeenCalled();
  });

  it('points at Prepare when no season is ready, and credits the ADP source', async () => {
    renderForm([{ path: SEASONS_PATH, reply: json(seasons({ results: [readiness(2023, false), readiness(2024, false)] })) }]);
    expect(await screen.findByText(/No season is ready to play yet\. Prepare one with a button below/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Create league and draft' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Prepare 2023 data' })).toBeEnabled();
    expect(screen.getByText(/downloads its results, ADP and projections, which takes about a minute/)).toBeInTheDocument();
    expect(screen.getByText('ADP: Fantasy Football Calculator')).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /Normal/ })).toBeChecked();
  });

  it('prepares a season, polls its progress and then offers it', async () => {
    // Polls land at once instead of every few seconds.
    const realSetTimeout = window.setTimeout.bind(window);
    vi.spyOn(window, 'setTimeout').mockImplementation(((handler: TimerHandler, delay?: number, ...rest: unknown[]) =>
      realSetTimeout(handler, delay === 3000 ? 5 : delay, ...rest)) as typeof window.setTimeout);
    const running = (step: string): ReplaySeasons => seasons({ prepare: { season: 2024, status: 'running', step, error: null } });
    const replies: ReplaySeasons[] = [
      seasons(),
      running('adp'),
      running('projections'),
      seasons({ results: [readiness(2022), readiness(2023), readiness(2024)], prepare: { season: 2024, status: 'done', step: null, error: null } }),
    ];
    let reads = 0;
    const { api, user } = renderForm([
      { path: SEASONS_PATH, reply: () => json(replies[Math.min(reads++, replies.length - 1)]) },
      { method: 'POST', path: `${SEASONS_PATH}/2024/prepare`, reply: json({ season: 2024, status: 'running', step: 'starting', error: null }) },
    ]);
    await user.click(await screen.findByRole('button', { name: 'Prepare 2024 data' }));

    expect(await screen.findByText('Preparing 2024: ADP…')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Prepare 2024 data' })).toBeDisabled();
    expect(await screen.findByText('Preparing 2024: projections…')).toBeInTheDocument();
    expect(await screen.findByText('2024 is prepared.')).toBeInTheDocument();
    expect(screen.getByRole('option', { name: '2024' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Prepare 2024 data' })).not.toBeInTheDocument();
    expect(api.callsTo(`${SEASONS_PATH}/2024/prepare`, 'POST')).toHaveLength(1);
    expect(api.callsTo(SEASONS_PATH)).toHaveLength(4);
  });

  it('keeps the form and keeps polling when one progress check fails', async () => {
    let reads = 0;
    // Polls land at once, except the one after the failure, which waits long enough to look at the page.
    const realSetTimeout = window.setTimeout.bind(window);
    vi.spyOn(window, 'setTimeout').mockImplementation(((handler: TimerHandler, delay?: number, ...rest: unknown[]) =>
      realSetTimeout(handler, delay === 3000 ? (reads === 3 ? 300 : 5) : delay, ...rest)) as typeof window.setTimeout);
    const running = (step: string): ReplaySeasons => seasons({ prepare: { season: 2024, status: 'running', step, error: null } });
    const done = seasons({ results: [readiness(2022), readiness(2023), readiness(2024)], prepare: { season: 2024, status: 'done', step: null, error: null } });
    const replies = [json(seasons()), json(running('adp')), json({ detail: 'busy' }, 500), json(running('projections')), json(done)];
    const { user } = renderForm([
      { path: SEASONS_PATH, reply: () => replies[Math.min(reads++, replies.length - 1)] },
      { method: 'POST', path: `${SEASONS_PATH}/2024/prepare`, reply: json({ season: 2024, status: 'running', step: 'starting', error: null }) },
    ]);
    await user.click(await screen.findByRole('button', { name: 'Prepare 2024 data' }));
    expect(await screen.findByText("Couldn't check the progress just now. Trying again…")).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Create league and draft' })).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();

    expect(await screen.findByText('2024 is prepared.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Create league and draft' })).toBeInTheDocument();
  });

  it('reports a failed import and lets the user try again', async () => {
    renderForm([{ path: SEASONS_PATH, reply: json(seasons({ prepare: { season: 2024, status: 'failed', step: null, error: 'HistoryError' } })) }]);
    expect(await screen.findByText('Preparing 2024 failed (HistoryError). Try again.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Prepare 2024 data' })).toBeEnabled();
  });
});
