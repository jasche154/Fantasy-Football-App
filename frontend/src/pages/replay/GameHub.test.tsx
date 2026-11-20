import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { replayAddDrop } from '../../api/replay';
import type { ReplaySummary } from '../../api/types';
import { json, mockFetch, type MockRoute } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import {
  actionResult, draftSummary, event, GAME_ID, MOSTERT, nextAction, playerDetail, RESULT_EVENTS, summary, teamRef, USER_TEAM_ID,
} from './fixtures';
import { GameHub } from './GameHub';
import type { ReplayView, ReplayViewProps } from './shared';

// Every render the screens see, in order (cleared per test).
const renders: { version: number; isBusy: boolean }[] = [];
beforeEach(() => { renders.length = 0; });

// The screens are other files' business: each is replaced by a probe that exercises the view contract.
function Probe({ name, game, version, act, isBusy, actError, openPlayer }: ReplayViewProps & { name: string }) {
  renders.push({ version, isBusy });
  return (
    <div>
      <p>{name} screen, version {version}{isBusy ? ', busy' : ''}</p>
      {actError && <p>screen saw: {actError}</p>}
      <button type="button" onClick={() => openPlayer(MOSTERT.id)}>Open Mostert</button>
      <button type="button" onClick={() => void act(() => replayAddDrop(game.id, { add: MOSTERT.id }))}>Add Mostert</button>
    </div>
  );
}
vi.mock('./DraftView', () => ({ DraftView: (props: ReplayViewProps) => <Probe name="draft" {...props} /> }));
vi.mock('./TeamView', () => ({ TeamView: (props: ReplayViewProps) => <Probe name="team" {...props} /> }));
vi.mock('./MatchupView', () => ({ MatchupView: (props: ReplayViewProps) => <Probe name="matchup" {...props} /> }));
vi.mock('./PlayersView', () => ({ PlayersView: (props: ReplayViewProps) => <Probe name="players" {...props} /> }));
vi.mock('./TradesView', () => ({ TradesView: (props: ReplayViewProps) => <Probe name="trades" {...props} /> }));
vi.mock('./LeagueView', () => ({ LeagueView: (props: ReplayViewProps) => <Probe name="league" {...props} /> }));

const GAME_PATH = `/api/replay/games/${GAME_ID}`;
const ADVANCE_PATH = `${GAME_PATH}/advance`;
const WAIVERS = summary({ phase: 'waivers', next_action: nextAction('wednesday') });

function renderHub(game: ReplaySummary, routes: MockRoute[] = [], view: ReplayView = 'team') {
  const api = mockFetch([META_ROUTE, ...routes, { path: GAME_PATH, reply: json(game) }]);
  renderWithProviders(<GameHub gameId={GAME_ID} view={view} />, { hash: `#/replay?game=${GAME_ID}` });
  return { api, user: userEvent.setup() };
}

describe('GameHub header', () => {
  it('shows the league, the record and this week\'s game with both projections', async () => {
    renderHub(summary());
    expect(await screen.findByRole('heading', { level: 1, name: 'Sunday Replay' })).toBeInTheDocument();
    expect(screen.getByText('2023 season · Week 5 · 12 teams · Half PPR')).toBeInTheDocument();
    const line = screen.getByText(/2nd of 12/).closest('p');
    expect(line).toHaveTextContent('Your Team 3-1 · 2nd of 12');
    expect(line).toHaveTextContent('Week 5 vs Sunday Scaries (2-2) · projected 118.40 – 104.75');
    expect(screen.getByRole('link', { name: 'All leagues' })).toHaveAttribute('href', '#/replay');
  });

  it('says so when the league cannot be opened', async () => {
    mockFetch([META_ROUTE, { path: GAME_PATH, reply: json({ detail: 'There is no league 7.' }, 404) }]);
    renderWithProviders(<GameHub gameId={GAME_ID} view="team" />);
    expect(await screen.findByRole('alert')).toHaveTextContent('There is no league 7.');
    expect(screen.getByRole('link', { name: 'Back to your leagues' })).toHaveAttribute('href', '#/replay');
  });
});

describe('GameHub primary button', () => {
  it('plays the week, then shows what happened and the next step', async () => {
    const nextWeek = summary({ week: 6, phase: 'waivers', next_action: nextAction('wednesday') });
    const after = { ...actionResult(nextWeek, RESULT_EVENTS), step: 'play', week: 5 };
    const { api, user } = renderHub(summary(), [{ method: 'POST', path: ADVANCE_PATH, reply: json(after) }]);
    await user.click(await screen.findByRole('button', { name: 'Play week 5' }));

    const panel = await screen.findByRole('region', { name: 'What just happened' });
    expect(api.callsTo(ADVANCE_PATH, 'POST')[0].body).toEqual({ step: 'play', week: 5 });
    expect(within(panel).getByRole('heading', { name: 'Results' })).toBeInTheDocument();
    expect(within(panel).getByText('Your Team beat Sunday Scaries 121.46-98.20')).toBeInTheDocument();
    // The hub stays busy for the one render that first shows the new version, so the button is enabled a render later.
    await waitFor(() => expect(screen.getByRole('button', { name: 'Sim to Wednesday (process waivers)' })).toBeEnabled());
    expect(screen.getByText('team screen, version 1')).toBeInTheDocument();

    await user.click(within(panel).getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByRole('region', { name: 'What just happened' })).not.toBeInTheDocument();
  });

  it('announces one short line for what happened, not the whole panel', async () => {
    const nextWeek = summary({ week: 6, phase: 'waivers', next_action: nextAction('wednesday') });
    const { user } = renderHub(summary(), [{ method: 'POST', path: ADVANCE_PATH, reply: json(actionResult(nextWeek, RESULT_EVENTS)) }]);
    await user.click(await screen.findByRole('button', { name: 'Play week 5' }));
    const live = screen.getByRole('status');
    await waitFor(() => expect(live).toHaveTextContent(
      'Your Team beat Sunday Scaries 121.46-98.20. 1 more update under “What just happened”.'));
    const panel = screen.getByRole('region', { name: 'What just happened' });
    expect(panel).not.toHaveAttribute('role', 'status');
    expect(screen.getAllByRole('status')).toEqual([live]);

    await user.click(within(panel).getByRole('button', { name: 'Dismiss' }));
    expect(live).toBeEmptyDOMElement();
  });

  it('has an empty live region from the start', async () => {
    renderHub(summary());
    await screen.findByRole('button', { name: 'Play week 5' });
    expect(screen.getByRole('status')).toBeEmptyDOMElement();
  });

  it('says what is happening and blocks a second press while the week is played', async () => {
    let finish: (() => void) | undefined;
    const reply = () => new Promise<ReturnType<typeof json>>((resolve) => { finish = () => resolve(json(actionResult(WAIVERS))); });
    const { api, user } = renderHub(summary(), [{ method: 'POST', path: ADVANCE_PATH, reply }]);
    await user.click(await screen.findByRole('button', { name: 'Play week 5' }));
    const button = await screen.findByRole('button', { name: 'Playing week 5…' });
    expect(button).toBeDisabled();
    expect(screen.getByText('team screen, version 0, busy')).toBeInTheDocument();
    finish?.();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Sim to Wednesday (process waivers)' })).toBeEnabled());
    expect(api.callsTo(ADVANCE_PATH, 'POST')).toHaveLength(1);
  });

  it('runs the waivers on "Sim to Wednesday"', async () => {
    const claims = [event(41, 'claim', 'Your Team claimed Raheem Mostert', { team: teamRef(USER_TEAM_ID), players: [MOSTERT] })];
    const { api, user } = renderHub(WAIVERS, [{ method: 'POST', path: ADVANCE_PATH, reply: json(actionResult(summary(), claims)) }]);
    await user.click(await screen.findByRole('button', { name: 'Sim to Wednesday (process waivers)' }));
    expect(await screen.findByRole('heading', { name: 'Waiver results' })).toBeInTheDocument();
    expect(api.callsTo(ADVANCE_PATH, 'POST')[0].body).toEqual({ step: 'wednesday', week: 5 });
    expect(screen.getByRole('button', { name: 'Play week 5' })).toBeInTheDocument();
  });

  it('shows only the draft during the draft, and "Make your pick" goes to it', async () => {
    const { api, user } = renderHub(draftSummary());
    await user.click(await screen.findByRole('button', { name: 'Make your pick' }));
    expect(screen.getByRole('region', { name: 'Draft' })).toHaveFocus();
    expect(screen.getByText('draft screen, version 0')).toBeInTheDocument();
    expect(screen.getByText('2023 season · Draft · 12 teams · Half PPR')).toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: 'League sections' })).not.toBeInTheDocument();
    expect(api.calls.filter((call) => call.method === 'POST')).toHaveLength(0);
  });

  it('puts focus on the season screen when the draft ends', async () => {
    const { user } = renderHub(draftSummary(), [{ method: 'POST', path: `${GAME_PATH}/add-drop`, reply: json(actionResult(summary())) }]);
    await user.click(await screen.findByRole('button', { name: 'Add Mostert' }));
    expect(await screen.findByText('team screen, version 1')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'My Team' })).toHaveFocus();
  });

  it('keeps focus on the primary button after playing a week', async () => {
    const { user } = renderHub(summary(), [{ method: 'POST', path: ADVANCE_PATH, reply: json(actionResult(WAIVERS)) }]);
    await user.click(await screen.findByRole('button', { name: 'Play week 5' }));
    expect(await screen.findByRole('button', { name: 'Sim to Wednesday (process waivers)' })).toHaveFocus();
  });

  it('is disabled once the season is complete, under a champion banner', async () => {
    renderHub(summary({ phase: 'complete', week: 16, next_action: nextAction('done'), champion: teamRef(1),
      record: { ...summary().record, place: 3 } }));
    expect(await screen.findByRole('button', { name: 'Season complete' })).toBeDisabled();
    expect(screen.getByText(/won the 2023 championship/).closest('p')).toHaveTextContent('Gridiron Ghosts won the 2023 championship. You finished 3rd.');
  });

  it('reloads the league and says so when the week had already moved on (409)', async () => {
    const moved = summary({ week: 6, next_action: { step: 'play', label: 'Play week 6' } });
    let loads = 0;
    const { api, user } = renderHub(summary(), [
      { path: GAME_PATH, reply: () => json((loads += 1) === 1 ? summary() : moved) },
      { method: 'POST', path: ADVANCE_PATH, reply: json({ detail: 'This league is in week 6, not week 5. Reload it.' }, 409) },
    ]);
    await user.click(await screen.findByRole('button', { name: 'Play week 5' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/already moved on.*reloaded/);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Play week 6' })).toBeEnabled());
    expect(api.callsTo(GAME_PATH)).toHaveLength(2);
    expect(screen.getByText('team screen, version 1')).toBeInTheDocument();
  });

  it('keeps the league on screen and shows the server\'s message when the reload after a 409 fails too', async () => {
    let loads = 0;
    const { user } = renderHub(summary(), [
      { path: GAME_PATH, reply: () => ((loads += 1) === 1 ? json(summary()) : json({ detail: 'Season data is not prepared.' }, 503)) },
      { method: 'POST', path: ADVANCE_PATH, reply: json({ detail: 'This league is in week 6, not week 5. Reload it.' }, 409) },
    ]);
    await user.click(await screen.findByRole('button', { name: 'Play week 5' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('This league is in week 6, not week 5. Reload it.');
    expect(screen.getByRole('heading', { level: 1, name: 'Sunday Replay' })).toBeInTheDocument();
    expect(screen.getByText('team screen, version 0')).toBeInTheDocument();
  });
});

describe('GameHub act', () => {
  it('surfaces an illegal move once in the alert and hands the message to the screen', async () => {
    const { user } = renderHub(summary(), [
      { method: 'POST', path: `${GAME_PATH}/add-drop`, reply: json({ detail: 'Your roster is full: choose a player to drop.' }, 422) },
    ]);
    await user.click(await screen.findByRole('button', { name: 'Add Mostert' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Your roster is full: choose a player to drop.');
    expect(screen.getByText('screen saw: Your roster is full: choose a player to drop.')).toBeInTheDocument();
    expect(screen.getByText('team screen, version 0')).toBeInTheDocument();
  });

  it('keeps the screens busy for the render that first shows a new version', async () => {
    const { user } = renderHub(summary(), [{ method: 'POST', path: `${GAME_PATH}/add-drop`, reply: json(actionResult(summary())) }]);
    await user.click(await screen.findByRole('button', { name: 'Add Mostert' }));
    expect(await screen.findByText('team screen, version 1')).toBeInTheDocument();
    expect(renders.find((render) => render.version === 1)).toEqual({ version: 1, isBusy: true });
    expect(renders.at(-1)).toEqual({ version: 1, isBusy: false });
  });

  it('groups a long run of events (results first) and cuts it until asked', async () => {
    const adds = Array.from({ length: 8 }, (_, index) => event(50 + index, 'add', `Blitz Brigade added player ${index + 1}`));
    const { user } = renderHub(summary(), [
      { method: 'POST', path: `${GAME_PATH}/add-drop`, reply: json(actionResult(summary(), [...adds, ...RESULT_EVENTS])) },
    ]);
    await user.click(await screen.findByRole('button', { name: 'Add Mostert' }));
    const panel = await screen.findByRole('region', { name: 'What just happened' });
    expect(within(panel).getAllByRole('heading', { level: 3 }).map((heading) => heading.textContent)).toEqual(['Results', 'Adds and drops']);
    expect(within(panel).queryByText('Blitz Brigade added player 8')).not.toBeInTheDocument();

    await user.click(within(panel).getByRole('button', { name: 'Show all 10' }));
    expect(within(panel).getByText('Blitz Brigade added player 8')).toBeInTheDocument();
    expect(within(panel).getByRole('button', { name: 'Show fewer' })).toHaveAttribute('aria-expanded', 'true');
  });
});

describe('GameHub tabs', () => {
  it('marks the current tab, shows pending counts and writes the tab to the URL', async () => {
    const { user } = renderHub(summary({ pending: { claims: 2, offers: 1 } }), [], 'matchup');
    const tabs = within(await screen.findByRole('navigation', { name: 'League sections' }));
    expect(tabs.getAllByRole('button').map((tab) => tab.textContent)).toEqual(
      ['My Team', 'Matchup', 'Players2 pending claims', 'Trades1 offers to answer', 'League']);
    expect(tabs.getByRole('button', { name: 'Matchup' })).toHaveAttribute('aria-current', 'page');
    expect(tabs.getByRole('button', { name: 'My Team' })).not.toHaveAttribute('aria-current');
    expect(screen.getByRole('region', { name: 'Matchup' })).toHaveTextContent('matchup screen');

    await user.click(tabs.getByRole('button', { name: /Players/ }));
    expect(window.location.hash).toBe(`#/replay?game=${GAME_ID}&view=players`);
  });
});

describe('GameHub player drawer', () => {
  it('opens with focus inside, shows played weeks only, and gives focus back on Escape', async () => {
    const { user } = renderHub(summary(), [{ path: `${GAME_PATH}/players/${MOSTERT.id}`, reply: json(playerDetail()) }]);
    const opener = await screen.findByRole('button', { name: 'Open Mostert' });
    await user.click(opener);

    const dialog = await screen.findByRole('dialog', { name: 'Raheem Mostert details' });
    expect(within(dialog).getByRole('button', { name: 'Close player details' })).toHaveFocus();
    expect(within(dialog).getByText(/Free agent/)).toBeInTheDocument();
    const rows = within(within(dialog).getByRole('table', { name: 'Raheem Mostert week by week' })).getAllByRole('row');
    expect(rows.map((row) => row.textContent)).toEqual([
      'WkOppProjPtsValue', '1DEN12.5011.0031.5', '2DEN12.5012.0031.5', '3BYE0.00—31.5', '4DEN12.5014.0031.5', '5DEN12.50—31.5',
    ]);

    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(opener).toHaveFocus();
  });

  it('shows no week table during the draft, when there are no weeks yet', async () => {
    const { user } = renderHub(draftSummary(), [{ path: `${GAME_PATH}/players/${MOSTERT.id}`, reply: json({ ...playerDetail(), weeks: [] }) }]);
    await user.click(await screen.findByRole('button', { name: 'Open Mostert' }));
    const dialog = await screen.findByRole('dialog', { name: 'Raheem Mostert details' });
    expect(await within(dialog).findByText(/season has not started/)).toBeInTheDocument();
    expect(within(dialog).queryByRole('table')).not.toBeInTheDocument();
  });

  it('closes from its button', async () => {
    const { user } = renderHub(summary(), [{ path: `${GAME_PATH}/players/${MOSTERT.id}`, reply: json(playerDetail()) }]);
    await user.click(await screen.findByRole('button', { name: 'Open Mostert' }));
    await user.click(await screen.findByRole('button', { name: 'Close player details' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Open Mostert' })).toHaveFocus();
  });
});
