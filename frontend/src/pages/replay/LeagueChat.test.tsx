import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';
import type { ReplayChat, ReplayPage } from '../../api/types';
import { json, mockFetch } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { readChatSeen } from './chatSeen';
import { GAME_ID, MOSTERT, OPPONENT_TEAM_ID, summary, teamRef, viewProps } from './fixtures';
import { GameNav } from './GameNav';
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

  it('still works when storage is blocked', () => {
    const original = window.localStorage.getItem;
    window.localStorage.getItem = () => { throw new Error('blocked'); };
    try {
      renderWithProviders(<GameNav game={summary({ chat: 99 })} view="team" />);
      expect(screen.getByText('(new messages)')).toBeInTheDocument();
    } finally {
      window.localStorage.getItem = original;
    }
  });
});
