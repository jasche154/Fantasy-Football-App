import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { ReplayDraft, ReplayDraftRow, ReplayPlayerRef } from '../../api/types';
import { json, mockFetch, type MockReply, type MockRoute } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { DraftView } from './DraftView';
import {
  actionResult, draftSummary, event, GAME_ID, HILL, KELCE, MAHOMES, MCCAFFREY, MOSTERT, summary, teamRef, TEAMS, TUCKER,
  USER_TEAM_ID, viewProps,
} from './fixtures';
import { useGame } from './useGame';

const GAME_PATH = `/api/replay/games/${GAME_ID}`;
const DRAFT_PATH = `${GAME_PATH}/draft`;
const PICK_PATH = `${DRAFT_PATH}/pick`;
const AUTO_PATH = `${DRAFT_PATH}/auto`;

function boardRow(player: ReplayPlayerRef, overrides: Partial<ReplayDraftRow> = {}): ReplayDraftRow {
  return { player, adp: 21.4, pos_rank: `${player.position}3`, rank: 18, projection: 17.25, ...overrides };
}

/** GET draft in round 2 of a 12-team draft: the user (slot 4) is up at 2.09 with McCaffrey already on the roster. */
function draft(overrides: Partial<ReplayDraft> = {}): ReplayDraft {
  return {
    total: 3, limit: 50, offset: 0,
    results: [boardRow(MAHOMES, { rank: 9, pos_rank: 'QB1' }), boardRow(MOSTERT), boardRow(TUCKER, { adp: null, projection: 8.4 })],
    phase: 'draft', on_the_clock: USER_TEAM_ID, is_user_turn: true, round: 2, overall: 21, rounds: 15, total_picks: 180, order: TEAMS,
    picks: [
      { overall: 4, round: 1, team: teamRef(USER_TEAM_ID), player: MCCAFFREY },
      { overall: 5, round: 1, team: teamRef(5), player: HILL },
      { overall: 20, round: 2, team: teamRef(5), player: KELCE },
    ],
    roster: { QB: [], RB: [MCCAFFREY], WR: [], TE: [], K: [], DST: [] },
    ...overrides,
  };
}

const FINISHED = draft({ total: 0, results: [], phase: 'open', on_the_clock: null, is_user_turn: false, round: null, overall: null });

/** The screen on its own: `act` sends the request but nothing is updated. */
function renderDraft(board: ReplayDraft | MockReply = draft(), routes: MockRoute[] = [], openPlayer = vi.fn()) {
  const reply = 'total' in board ? json(board) : board;
  const api = mockFetch([META_ROUTE, ...routes, { path: DRAFT_PATH, reply }]);
  renderWithProviders(<DraftView {...viewProps({ game: draftSummary(), openPlayer })} />);
  return { api, user: userEvent.setup(), openPlayer, lastQuery: () => api.callsTo(DRAFT_PATH).at(-1)?.url.searchParams };
}

/** The screen wired to the real `act`, the way the hub mounts it (the draft only shows while phase is "draft"). */
function Room() {
  const { game, version, act, isBusy, actError } = useGame(GAME_ID);
  if (!game) return null;
  if (game.phase !== 'draft') return <p>The season has started</p>;
  return (
    <>
      {actError && <p role="alert">{actError}</p>}
      <DraftView game={game} version={version} act={act} isBusy={isBusy} actError={actError} openPlayer={() => undefined} />
    </>
  );
}

function renderRoom(routes: MockRoute[]) {
  const api = mockFetch([META_ROUTE, ...routes, { path: GAME_PATH, reply: json(draftSummary()) }, { path: DRAFT_PATH, reply: json(draft()) }]);
  renderWithProviders(<Room />);
  return { api, user: userEvent.setup() };
}

const board = () => screen.findByRole('table', { name: 'Available players in draft order' });

describe('DraftView', () => {
  it('shows the pick on the clock, the board in draft order and the attribution', async () => {
    const { lastQuery } = renderDraft();
    const rows = within(await board()).getAllByRole('row');

    expect(screen.getByText("You're on the clock").closest('[role="status"]')).toHaveTextContent('Round 2, pick 9 · 21 of 180 overall');
    expect(rows).toHaveLength(4);
    expect(rows[1]).toHaveTextContent('1Patrick MahomesQBKCQB121.417.25Draft Patrick Mahomes');
    // The board is in ADP order, so the first column is the row's place in it, not the value rank (9, 18, 18).
    expect(within(rows[0]).getAllByRole('columnheader')[0]).toHaveTextContent(/^#$/);
    expect(rows.slice(1).map((row) => within(row).getAllByRole('cell')[0].textContent)).toEqual(['1', '2', '3']);
    expect(within(rows[3]).getByText('—')).toBeInTheDocument();                  // no ADP for the kicker
    expect(within(rows[2]).getByRole('button', { name: 'Draft Raheem Mostert' })).toBeEnabled();
    expect(screen.getByText('Showing 3 of 3 players')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Show more' })).not.toBeInTheDocument();
    expect(screen.getByText('ADP: Fantasy Football Calculator')).toBeInTheDocument();
    expect(lastQuery()?.toString()).toBe('limit=50');
  });

  it('lists my roster by position against the lineup, and the picks most recent first', async () => {
    renderDraft();
    const roster = await screen.findByRole('region', { name: 'My roster' });
    expect(roster).toHaveTextContent('1 of 15 picks made');
    expect(within(roster).getByText('Christian McCaffrey').closest('div')).toHaveTextContent('RB 1 drafted · 2 start');
    expect(roster).toHaveTextContent('Lineup: 1 QB, 2 RB, 2 WR, 1 TE, 1 FLEX, 1 K, 1 DST and 6 bench spots.');

    const log = within(screen.getByRole('table', { name: 'Picks so far, most recent first' })).getAllByRole('row');
    expect(log[1]).toHaveTextContent('2.08Hail MarysTravis Kelce');
    expect(log[3]).toHaveTextContent('1.04Your Team (you)Christian McCaffrey');
    expect(log[3]).toHaveClass('row--mine');
    expect(screen.getByRole('region', { name: 'Picks' })).toHaveTextContent('3 of 180 made');
  });

  it('switches the picks to a grid with teams across and rounds down', async () => {
    const { user } = renderDraft();
    await user.click(await screen.findByRole('button', { name: 'Grid' }));

    expect(screen.getByRole('button', { name: 'Grid' })).toHaveAttribute('aria-pressed', 'true');
    const grid = screen.getByRole('table', { name: 'Draft grid: teams across, rounds down' });
    expect(within(grid).getAllByRole('columnheader')).toHaveLength(13);
    expect(within(grid).getByRole('columnheader', { name: 'Your Team (you)' })).toHaveClass('replay-draft__mine');
    const [, first, second, third] = within(grid).getAllByRole('row');
    expect(within(first).getAllByRole('cell')[3]).toHaveTextContent('Christian McCaffrey1.04 · RB');
    expect(within(second).getAllByRole('cell')[3]).toHaveTextContent('On the clock');
    expect(within(second).getAllByRole('cell')[4]).toHaveTextContent('Travis Kelce2.08 · TE');
    expect(third).toHaveTextContent(/^3$/);
    expect(within(grid).getAllByRole('row')).toHaveLength(16);
  });

  it('filters by position, searches after a pause and shows more on request', async () => {
    const { user, lastQuery } = renderDraft(draft({ total: 120 }));
    await user.click(await screen.findByRole('button', { name: 'Show more' }));
    await waitFor(() => expect(lastQuery()?.get('limit')).toBe('100'));

    await user.click(screen.getByRole('button', { name: 'K' }));
    await waitFor(() => expect(lastQuery()?.toString()).toBe('position=K&limit=50'));
    expect(screen.getByRole('button', { name: 'K' })).toHaveAttribute('aria-pressed', 'true');

    await user.type(screen.getByRole('searchbox', { name: 'Search' }), ' tuck ');
    await waitFor(() => expect(lastQuery()?.toString()).toBe('position=K&search=tuck&limit=50'));
  });

  it('says so when the longest page still leaves players out', async () => {
    const { user } = renderDraft(draft({ total: 320 }));
    for (let presses = 0; presses < 3; presses += 1) await user.click(await screen.findByRole('button', { name: 'Show more' }));
    expect(await screen.findByText('Showing 3 of 320 players: search or filter by position to find the rest')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Show more' })).not.toBeInTheDocument();
  });

  it('opens the player drawer from the board', async () => {
    const { user, openPlayer } = renderDraft();
    await user.click(within(await board()).getByRole('button', { name: 'Raheem Mostert' }));
    expect(openPlayer).toHaveBeenCalledWith(MOSTERT.id);
  });

  it('locks every pick button while another team is on the clock', async () => {
    renderDraft(draft({ on_the_clock: 5, is_user_turn: false }));
    expect(await screen.findByText('Hail Marys is on the clock')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Draft Patrick Mahomes' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Auto-pick for me' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Auto-draft the rest' })).toBeDisabled();
  });

  it('survives the empty page a finished draft answers', async () => {
    renderDraft(FINISHED);
    expect(await screen.findByText('The draft is complete')).toBeInTheDocument();
    expect(screen.getByText('The draft is over')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Auto-pick for me' })).toBeDisabled();
    expect(screen.getByRole('table', { name: 'Picks so far, most recent first' })).toBeInTheDocument();
  });

  it('says when no player matches the filters', async () => {
    renderDraft(draft({ total: 0, results: [] }));
    expect(await screen.findByText('No players match')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Auto-pick for me' })).toBeEnabled();
  });

  it('shows the server message and a retry when the draft cannot be loaded', async () => {
    renderDraft(json({ detail: 'The 2023 season data is not prepared.' }, 503));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('The 2023 season data is not prepared.');
    expect(within(alert).getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });
});

describe('DraftView actions', () => {
  it('drafts a player once, shows the pick as pending and reloads the board', async () => {
    let finish: (() => void) | undefined;
    const answer = actionResult(draftSummary(), [event(41, 'draft', 'Hail Marys drafted Tyreek Hill', { week: 0 })]);
    const reply = () => new Promise<MockReply>((resolve) => { finish = () => resolve(json(answer)); });
    const { api, user } = renderRoom([{ method: 'POST', path: PICK_PATH, reply }]);
    await user.click(await screen.findByRole('button', { name: 'Draft Raheem Mostert' }));

    expect(await screen.findByRole('button', { name: 'Drafting… Raheem Mostert' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Draft Patrick Mahomes' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Auto-pick for me' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Auto-draft the rest' })).toBeDisabled();
    expect(api.callsTo(PICK_PATH, 'POST')).toHaveLength(1);
    expect(api.callsTo(PICK_PATH, 'POST')[0].body).toEqual({ player_id: MOSTERT.id });

    finish?.();
    await waitFor(() => expect(api.callsTo(DRAFT_PATH)).toHaveLength(2));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Draft Patrick Mahomes' })).toBeEnabled());
    expect(screen.getByText("You're on the clock").closest('[role="status"]')).toHaveFocus();
  });

  it('shows a refused pick next to the button and reloads the board', async () => {
    const refused = json({ detail: 'Raheem Mostert has already been drafted.' }, 422);
    const { api, user } = renderRoom([{ method: 'POST', path: PICK_PATH, reply: refused }]);
    await user.click(await screen.findByRole('button', { name: 'Draft Raheem Mostert' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Raheem Mostert has already been drafted.');
    const row = screen.getByRole('button', { name: 'Draft Raheem Mostert' }).closest('tr');
    expect(row).toHaveTextContent('Raheem Mostert has already been drafted.');
    await waitFor(() => expect(api.callsTo(DRAFT_PATH)).toHaveLength(2));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Draft Raheem Mostert' })).toBeEnabled());
  });

  it('auto-picks one player for me', async () => {
    const { api, user } = renderRoom([{ method: 'POST', path: AUTO_PATH, reply: json(actionResult(draftSummary())) }]);
    await user.click(await screen.findByRole('button', { name: 'Auto-pick for me' }));

    await waitFor(() => expect(api.callsTo(DRAFT_PATH)).toHaveLength(2));
    const [call] = api.callsTo(AUTO_PATH, 'POST');
    expect(call.url.search).toBe('');
    expect(call.body).toBeUndefined();
  });

  it('auto-drafts the rest only after a second, confirming press', async () => {
    const { api, user } = renderRoom([{ method: 'POST', path: AUTO_PATH, reply: json(actionResult(summary())) }]);
    await user.click(await screen.findByRole('button', { name: 'Auto-draft the rest' }));
    expect(api.callsTo(AUTO_PATH, 'POST')).toHaveLength(0);

    await user.click(screen.getByRole('button', { name: 'Yes, draft the rest for me' }));
    expect(await screen.findByText('The season has started')).toBeInTheDocument();
    expect(api.callsTo(AUTO_PATH, 'POST')[0].url.search).toBe('?whole_draft=true');
  });
});
