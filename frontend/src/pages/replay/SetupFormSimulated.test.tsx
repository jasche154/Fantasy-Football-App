import { act, fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { json, mockFetch, type MockReply, type MockRoute } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { actionResult, draftSummary, readiness, seasons, simulatedSeason } from './fixtures';
import { nowSummary } from './SeasonChoice';
import { SetupForm } from './SetupForm';

const SEASONS_PATH = '/api/replay/seasons';
const GAMES_PATH = '/api/replay/games';
const CREATED = draftSummary({ id: 21, season: 2026, kind: 'live', sim_from: 10, start_week: 10 });
const BOTH = seasons({ simulated: [simulatedSeason('live'), simulatedSeason('future')] });

function renderForm(routes: MockRoute[]) {
  const api = mockFetch([META_ROUTE, ...routes]);
  const onCreated = vi.fn();
  renderWithProviders(<SetupForm onCreated={onCreated} />);
  return { api, onCreated, user: userEvent.setup() };
}

const createRoute = (reply: MockReply | (() => Promise<MockReply>) = json(actionResult(CREATED))): MockRoute =>
  ({ method: 'POST', path: GAMES_PATH, reply });

describe('SetupForm simulated seasons', () => {
  it('sets the live and the future season apart from the past ones, each saying what it is', async () => {
    renderForm([{ path: SEASONS_PATH, reply: json(BOTH) }]);
    const group = await screen.findByRole('group', { name: 'What to play' });

    const choices = within(group).getAllByRole('radio');
    expect(choices.map((radio) => radio.closest('label')?.textContent)).toEqual([
      'Past seasonA finished NFL season, replayed week by week with the real results.',
      '2026 · liveReal through week 9, then simulated: every league plays out its own rest of the season.',
      '2027 · simulated seasonNext year’s rookies and schedule, the whole season simulated: every league different.',
    ]);
    expect(choices[0]).toBeChecked();
    // "Random season" stays a past season: it lives in the past seasons' list.
    expect(within(screen.getByLabelText('Season')).getAllByRole('option').map((option) => option.textContent))
      .toEqual(['2022', '2023', 'Random season']);
  });

  it('shows a season that is not ready with its reason, and the live season gets a Prepare button', async () => {
    const reason = 'Prepare 2026 first: weeks 1-9 are not all imported.';
    const { api, user } = renderForm([
      { path: SEASONS_PATH, reply: json(seasons({ simulated: [
        simulatedSeason('live', { ready: false, can_start_now: false, reason }),
        simulatedSeason('future', { ready: false, can_start_now: false, reason: 'The 2027 season is not built yet.' }),
      ] })) },
      { method: 'POST', path: `${SEASONS_PATH}/2026/prepare`, reply: json({ season: 2026, status: 'running', step: 'starting', error: null }) },
    ]);

    const live = await screen.findByRole('radio', { name: /2026 · live/ });
    expect(live).toBeDisabled();
    expect(live.closest('label')).toHaveTextContent(reason);
    expect(screen.getByRole('radio', { name: /2027/ })).toBeDisabled();
    expect(screen.getByRole('radio', { name: /2027/ }).closest('label')).toHaveTextContent('The 2027 season is not built yet.');
    await user.click(screen.getByRole('button', { name: 'Prepare 2026 data' }));
    expect(api.callsTo(`${SEASONS_PATH}/2026/prepare`, 'POST')).toHaveLength(1);
  });

  it('creates a live league that jumps in now, showing what the league will get', async () => {
    const { api, onCreated, user } = renderForm([{ path: SEASONS_PATH, reply: json(BOTH) }, createRoute()]);
    await user.click(await screen.findByRole('radio', { name: /2026 · live/ }));
    expect(screen.queryByLabelText('Season')).not.toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /Full season/ })).toBeChecked();

    await user.click(screen.getByRole('radio', { name: /Jump in now \(draft today, play week 10 on\)/ }));
    // The bracket that keeps the most weeks: 4 teams, and what that means in weeks.
    expect(screen.getByLabelText('Playoff teams')).toHaveValue('4');
    expect(screen.getByText(nowSummary(9, 17, 4))).toHaveTextContent('plays weeks 10-15 (6 weeks), then the playoffs in weeks 16-17. Trade deadline: week 13.');
    await user.selectOptions(screen.getByLabelText('Playoff teams'), '6');
    expect(screen.getByText(/plays weeks 10-14 \(5 weeks\)/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Create league and draft' }));

    await vi.waitFor(() => expect(onCreated).toHaveBeenCalledWith(CREATED));
    expect(api.callsTo(GAMES_PATH, 'POST')[0].body).toEqual({
      season: 2026, teams: 12, scoring: 'half', waiver_mode: 'wednesday', difficulty: 'normal', playoff_end_week: 17, playoff_teams: 6,
      start: 'now',
    });
  });

  it('creates a full-season live league, and a future league without a start', async () => {
    const { api, user } = renderForm([{ path: SEASONS_PATH, reply: json(BOTH) }, createRoute()]);
    await user.click(await screen.findByRole('radio', { name: /2026 · live/ }));
    await user.click(screen.getByRole('button', { name: 'Create league and draft' }));
    await vi.waitFor(() => expect(api.callsTo(GAMES_PATH, 'POST')).toHaveLength(1));
    expect(api.callsTo(GAMES_PATH, 'POST')[0].body).toMatchObject({ season: 2026, start: 'full' });

    await user.click(screen.getByRole('radio', { name: /2027/ }));
    expect(screen.queryByRole('group', { name: 'Start' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Create league and draft' }));
    await vi.waitFor(() => expect(api.callsTo(GAMES_PATH, 'POST')).toHaveLength(2));
    expect(api.callsTo(GAMES_PATH, 'POST')[1].body).toMatchObject({ season: 2027 });
    expect(api.callsTo(GAMES_PATH, 'POST')[1].body).not.toHaveProperty('start');
  });

  it('turns "Jump in now" off with the reason when too few weeks are left', async () => {
    renderForm([{ path: SEASONS_PATH, reply: json(seasons({ simulated: [simulatedSeason('live', { through: 14, can_start_now: false })] })) }]);
    await userEvent.setup().click(await screen.findByRole('radio', { name: /2026 · live/ }));

    const now = screen.getByRole('radio', { name: /Jump in now/ });
    expect(now).toBeDisabled();
    expect(now.closest('label')).toHaveTextContent('Not available: too few regular-season weeks are left after week 14.');
    expect(screen.getByRole('radio', { name: /Full season/ })).toBeChecked();
  });

  it('disables the fields while the season is simulated and sends one request however often it is pressed', async () => {
    let answer: (reply: MockReply) => void = () => undefined;
    const { api, onCreated } = renderForm([
      { path: SEASONS_PATH, reply: json(BOTH) },
      createRoute(() => new Promise((resolve) => { answer = resolve; })),
    ]);
    await userEvent.setup().click(await screen.findByRole('radio', { name: /2027/ }));
    const create = screen.getByRole('button', { name: 'Create league and draft' });
    // Two presses before any re-render: only the in-flight guard can stop the second one.
    act(() => {
      fireEvent.click(create);
      fireEvent.click(create);
    });

    expect(await screen.findByRole('button', { name: 'Simulating the season…' })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByText('Simulating the 2027 season for your league. This takes about 10 seconds.')).toBeInTheDocument();
    expect(screen.getByLabelText('League name')).toBeDisabled();
    expect(screen.getByRole('radio', { name: /Past season/ })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Simulating the season…' }));
    expect(api.callsTo(GAMES_PATH, 'POST')).toHaveLength(1);

    answer(json(actionResult(CREATED)));
    await vi.waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
  });

  it('keeps focus on the busy button (inert to Enter) and says what is happening in a status region that was already there', async () => {
    let answer: (reply: MockReply) => void = () => undefined;
    const message = 'The 2027 season could not be built.';
    const { api, user } = renderForm([
      { path: SEASONS_PATH, reply: json(BOTH) },
      createRoute(() => new Promise((resolve) => { answer = resolve; })),
    ]);
    await user.click(await screen.findByRole('radio', { name: /2027/ }));
    const regions = screen.getAllByRole('status');
    // Enter in a field: the field is disabled while the league is built, so focus moves to the button, not <body>.
    await user.type(screen.getByLabelText('League name'), 'Sunday{Enter}');

    const busy = await screen.findByRole('button', { name: 'Simulating the season…' });
    expect(busy).toHaveFocus();
    expect(busy).toBeEnabled();
    expect(regions).toContain(screen.getByText(/^Simulating the 2027 season/).closest('[role="status"]'));
    await user.keyboard('{Enter}');
    expect(api.callsTo(GAMES_PATH, 'POST')).toHaveLength(1);

    answer(json({ detail: message }, 422));
    expect(await screen.findByRole('alert')).toHaveTextContent(message);
    expect(screen.getByRole('button', { name: 'Create league and draft' })).toHaveFocus();
  });

  it('fills the jump-in summary into a status region mounted with the start choice', async () => {
    const { user } = renderForm([{ path: SEASONS_PATH, reply: json(BOTH) }]);
    await user.click(await screen.findByRole('radio', { name: /2026 · live/ }));
    const regions = screen.getAllByRole('status');
    await user.click(screen.getByRole('radio', { name: /Jump in now/ }));
    expect(regions).toContain(screen.getByText(nowSummary(9, 17, 4)).closest('[role="status"]'));
  });

  it('offers no live season when there is none: a finished season is an ordinary past season, next to the future one', async () => {
    renderForm([{ path: SEASONS_PATH, reply: json(seasons({
      results: [readiness(2024), readiness(2025), readiness(2026)],
      simulated: [simulatedSeason('future', { season: 2027 })],
    })) }]);
    const group = await screen.findByRole('group', { name: 'What to play' });

    expect(within(group).getAllByRole('radio').map((radio) => radio.closest('label')?.querySelector('.radio-card__title')?.textContent))
      .toEqual(['Past season', '2027 · simulated season']);
    expect(screen.queryByRole('group', { name: 'Start' })).not.toBeInTheDocument();
    expect(within(screen.getByLabelText('Season')).getAllByRole('option').map((option) => option.textContent))
      .toEqual(['2024', '2025', '2026', 'Random season']);
    expect(screen.getByLabelText('Season')).toHaveValue('2026');
  });

  it('shows a new live season next to a future season that is not ready, with its reason', async () => {
    const reason = 'offseason_2028.json: no such file. Add the 2028 offseason file.';
    const { api, user } = renderForm([
      { path: SEASONS_PATH, reply: json(seasons({
        results: [readiness(2026)],
        simulated: [simulatedSeason('live', { season: 2027, through: 1 }),
          simulatedSeason('future', { season: 2028, ready: false, reason })],
      })) },
      createRoute(),
    ]);

    const future = await screen.findByRole('radio', { name: /2028 · simulated season/ });
    expect(future).toBeDisabled();
    expect(future.closest('label')).toHaveTextContent(reason);
    await user.click(screen.getByRole('radio', { name: /2027 · live/ }));
    expect(screen.getByRole('radio', { name: /2027 · live/ }).closest('label')).toHaveTextContent('Real through week 1');
    await user.click(screen.getByRole('button', { name: 'Create league and draft' }));
    await vi.waitFor(() => expect(api.callsTo(GAMES_PATH, 'POST')).toHaveLength(1));
    expect(api.callsTo(GAMES_PATH, 'POST')[0].body).toMatchObject({ season: 2027, start: 'full' });
  });

  it('shows the server’s refusal next to the button and lets the user change the rules', async () => {
    const message = 'Only 2 regular-season weeks are left after week 9, and a league that starts now needs 3.';
    const { user } = renderForm([{ path: SEASONS_PATH, reply: json(BOTH) }, createRoute(json({ detail: message }, 422))]);
    await user.click(await screen.findByRole('radio', { name: /2026 · live/ }));
    await user.click(screen.getByRole('radio', { name: /Jump in now/ }));
    await user.click(screen.getByRole('button', { name: 'Create league and draft' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(message);
    expect(screen.getByLabelText('Playoff teams')).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Create league and draft' })).toBeEnabled();
  });
});
