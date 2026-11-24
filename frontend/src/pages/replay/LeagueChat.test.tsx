import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';
import type { ReplayChat, ReplayPage } from '../../api/types';
import { AppProviders } from '../../App';
import { json, mockFetch, type MockRoute } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { forgetChatSeen, markChatSeen, readChatSeen } from './chatSeen';
import { GAME_ID, MOSTERT, OPPONENT_TEAM_ID, summary, teamRef, viewProps } from './fixtures';
import { GameNav } from './GameNav';
import { LeagueChat } from './LeagueChat';
import { LeagueView } from './LeagueView';

const CHAT_PATH = `/api/replay/games/${GAME_ID}/chat`;

function line(overrides: Partial<ReplayChat> = {}): ReplayChat {
  return { id: 1, week: 4, team: teamRef(2), to_user: false, kind: 'boom', text: `${MOSTERT.name} with 31.2. Love to see it.`,
    players: [MOSTERT], ...overrides };
}

function page(results: ReplayChat[], total = results.length): ReplayPage<ReplayChat> {
  return { total, limit: 25, offset: 0, results };
}

afterEach(() => window.localStorage.clear());

describe('League chat', () => {
  it('lists the lines newest first, highlights the ones to the user, and marks them seen', async () => {
    const lines = [
      line({ id: 3, team: teamRef(OPPONENT_TEAM_ID), to_user: true, kind: 'close_win', text: 'Whew. By 2.1. Good game Your Team.', players: [] }),
      line(),
    ];
    mockFetch([META_ROUTE, { path: `/api/replay/games/${GAME_ID}/standings`, reply: json({ results: [], regular_season_weeks: 14,
      playoff_teams: 4, playoff_weeks: [15, 16], byes: 0, seeds_final: false }) }, { path: CHAT_PATH, reply: json(page(lines)) }]);
    renderWithProviders(<LeagueView {...viewProps({ game: summary({ chat: 2 }) })} />);
    const user = userEvent.setup();

    expect(screen.getByRole('button', { name: /Chat.*new messages/ })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Chat/ }));
    const feed = await screen.findByRole('list', { name: 'League chat, newest first' });
    const items = within(feed).getAllByRole('listitem');

    expect(items[0]).toHaveTextContent('Whew. By 2.1.');
    expect(items[0]).toHaveTextContent('to you');
    expect(items[0]).toHaveClass('game-chat__line--to-you');
    expect(items[1]).not.toHaveClass('game-chat__line--to-you');
    expect(within(items[1]).getByRole('button', { name: MOSTERT.name })).toBeInTheDocument();
    expect(readChatSeen(GAME_ID)).toBe(2);
    expect(screen.queryByText('(new messages)')).not.toBeInTheDocument();
  });

  it('labels lines with the real NFL week in a simulated league that started mid-season', async () => {
    const live = summary({ kind: 'live', sim_from: 6, start_week: 6, week: 8, chat: 1 });
    mockFetch([META_ROUTE, { path: CHAT_PATH, reply: json(page([line({ week: 7, text: 'Two straight. Feels good.' })])) },
      { path: `/api/replay/games/${GAME_ID}/standings`, reply: json({ results: [], regular_season_weeks: 15, playoff_teams: 4,
        playoff_weeks: [16, 17], byes: 0, seeds_final: false }) }]);
    renderWithProviders(<LeagueView {...viewProps({ game: live })} />);
    await userEvent.setup().click(screen.getByRole('button', { name: /Chat/ }));
    const item = within(await screen.findByRole('list', { name: 'League chat, newest first' })).getByRole('listitem');

    expect(item).toHaveTextContent('Wk 7');
    expect(item).toHaveTextContent('Two straight. Feels good.');
  });

  it('says so when nobody has talked yet', async () => {
    mockFetch([META_ROUTE, { path: CHAT_PATH, reply: json(page([])) },
      { path: `/api/replay/games/${GAME_ID}/standings`, reply: json({ results: [], regular_season_weeks: 14, playoff_teams: 4,
        playoff_weeks: [15, 16], byes: 0, seeds_final: false }) }]);
    renderWithProviders(<LeagueView {...viewProps()} />);
    await userEvent.setup().click(screen.getByRole('button', { name: 'Chat' }));

    expect(await screen.findByText('Quiet so far')).toBeInTheDocument();
  });
});

describe('GameNav unread dot', () => {
  it('shows a dot with words on League while the chat has unseen lines', () => {
    window.localStorage.setItem(`replay-chat-seen:${GAME_ID}`, '3');
    const { unmount } = renderWithProviders(<GameNav game={summary({ chat: 5 })} view="team" />);
    expect(screen.getByRole('link', { name: /League.*new messages/ })).toBeInTheDocument();
    unmount();

    renderWithProviders(<GameNav game={summary({ chat: 3 })} view="team" />);
    expect(screen.queryByText('(new messages)')).not.toBeInTheDocument();
  });

  it('still works when storage is blocked, remembering what was read in memory', () => {
    const { getItem, setItem } = window.localStorage;
    window.localStorage.getItem = () => { throw new Error('blocked'); };
    window.localStorage.setItem = () => { throw new Error('blocked'); };
    try {
      const blocked = GAME_ID + 500;            // a league no other test has marked in this page's memory
      renderWithProviders(<GameNav game={summary({ id: blocked, chat: 99 })} view="team" />);
      expect(screen.getByText('(new messages)')).toBeInTheDocument();
      act(() => markChatSeen(blocked, 99));
      expect(screen.queryByText('(new messages)')).not.toBeInTheDocument();
    } finally {
      window.localStorage.getItem = getItem;
      window.localStorage.setItem = setItem;
    }
  });

  it("shows the dot in a new league that reuses a deleted league's id", () => {
    window.localStorage.setItem(`replay-chat-seen:${GAME_ID}`, '140');
    renderWithProviders(<GameNav game={summary({ chat: 3 })} view="team" />);
    expect(screen.getByRole('link', { name: /League.*new messages/ })).toBeInTheDocument();
  });

  it("forgets a deleted league's count", () => {
    window.localStorage.setItem(`replay-chat-seen:${GAME_ID}`, '12');
    forgetChatSeen(GAME_ID);
    expect(window.localStorage.getItem(`replay-chat-seen:${GAME_ID}`)).toBeNull();
    expect(readChatSeen(GAME_ID)).toBe(0);
  });

  it('stays hidden on League while the chat is open and the newest lines are still loading', async () => {
    window.localStorage.setItem(`replay-chat-seen:${GAME_ID}`, '5');
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => { release = resolve; });
    mockFetch([META_ROUTE, { path: CHAT_PATH, reply: async () => { await held; return json(page([line()], 7)); } }]);
    const game = summary({ chat: 7 });
    renderWithProviders(<><GameNav game={game} view="league" /><LeagueChat {...viewProps({ game })} /></>);

    expect(screen.queryByText('(new messages)')).not.toBeInTheDocument();
    release();
    await waitFor(() => expect(readChatSeen(GAME_ID)).toBe(7));
    expect(screen.queryByText('(new messages)')).not.toBeInTheDocument();
  });
});

describe('League chat paging', () => {
  const many = (offset: number) => Array.from({ length: 25 }, (_, n) => line({ id: 60 - offset - n, text: `Line ${60 - offset - n}` }));

  function chatRoute(seen: number[]): MockRoute {
    return { path: CHAT_PATH, reply: ({ url }) => {
      const offset = Number(url.searchParams.get('offset'));
      seen.push(offset);
      return json({ total: 60, limit: 25, offset, results: many(offset) });
    } };
  }

  it('keeps focus on a pager button when Newer reaches the first page', async () => {
    mockFetch([META_ROUTE, chatRoute([])]);
    renderWithProviders(<LeagueChat {...viewProps({ game: summary({ chat: 60 }) })} />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Older' }));
    await screen.findByText('26–50 of 60');
    await user.click(screen.getByRole('button', { name: 'Newer' }));
    await screen.findByText('1–25 of 60');

    expect(screen.getByRole('button', { name: 'Newer' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Older' })).toHaveFocus();
  });

  it('goes back to the newest lines when the game moves on', async () => {
    const offsets: number[] = [];
    mockFetch([META_ROUTE, chatRoute(offsets)]);
    const { rerender } = renderWithProviders(<LeagueChat {...viewProps({ game: summary({ chat: 60 }) })} />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Older' }));
    await screen.findByText('26–50 of 60');
    rerender(<AppProviders><LeagueChat {...viewProps({ game: summary({ chat: 60 }), version: 1 })} /></AppProviders>);

    expect(await screen.findByText('1–25 of 60')).toBeInTheDocument();
    expect(offsets).toEqual([0, 25, 0]);
  });
});
