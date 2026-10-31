import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { json, mockFetch, type MockRoute } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { AskPage } from './AskPage';
import { chatReply, league } from './askFixtures';
import { STORAGE_KEY } from './chatStorage';

const CHAT = '/api/agent/chat';
const NO_LEAGUES: MockRoute = { path: '/api/my-team/leagues', reply: json({ leagues: [] }) };
const TWO_LEAGUES: MockRoute = { path: '/api/my-team/leagues', reply: json({ leagues: [league(1, 'Chen'), league(2, 'Tintin')] }) };

function composer(): HTMLElement {
  return screen.getByRole('textbox', { name: 'Ask a fantasy football question' });
}

function lastBody(api: ReturnType<typeof mockFetch>): { messages: { content: string }[]; league_id?: number } {
  const calls = api.callsTo(CHAT, 'POST');
  return calls[calls.length - 1].body as { messages: { content: string }[]; league_id?: number };
}

describe('AskPage recovery', () => {
  it('blocks sending during a rate-limit wait and shows it next to Send', async () => {
    const user = userEvent.setup();
    const api = mockFetch([META_ROUTE, NO_LEAGUES, { method: 'POST', path: CHAT,
      reply: { status: 429, body: { detail: 'limit' }, headers: { 'Retry-After': '30' } } }]);
    renderWithProviders(<AskPage />);

    await user.type(composer(), 'Who?{Enter}');
    expect(await screen.findByText(/^Wait \d+ s$/)).toBeInTheDocument();
    await user.type(composer(), ' again{Enter}');

    expect(api.callsTo(CHAT, 'POST')).toHaveLength(1);
    expect(composer()).toHaveValue('Who? again');
  });

  it('retries with the edited draft rather than the original question', async () => {
    const user = userEvent.setup();
    const api = mockFetch([META_ROUTE, NO_LEAGUES, { method: 'POST', path: CHAT, reply: json({ detail: 'Upstream timeout' }, 504) }]);
    renderWithProviders(<AskPage />);

    await user.type(composer(), 'Who?{Enter}');
    await screen.findByRole('alert');
    expect(composer()).toHaveValue('Who?');
    await user.clear(composer());
    await user.type(composer(), 'Who now?');
    await user.click(screen.getByRole('button', { name: 'Try again' }));

    await waitFor(() => expect(api.callsTo(CHAT, 'POST')).toHaveLength(2));
    expect(lastBody(api).messages).toEqual([{ role: 'user', content: 'Who now?' }]);
  });

  it('does not save an unanswered question when the page closes mid-reply', async () => {
    const user = userEvent.setup();
    mockFetch([META_ROUTE, NO_LEAGUES, { method: 'POST', path: CHAT, reply: () => new Promise(() => undefined) }]);
    const first = renderWithProviders(<AskPage />);

    await user.type(composer(), 'Is Puka a buy?{Enter}');
    expect(await screen.findByText('Looking up the data…')).toBeInTheDocument();
    first.unmount();

    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
    renderWithProviders(<AskPage />);
    expect(screen.queryByText('Is Puka a buy?')).not.toBeInTheDocument();
  });

  it('drops a saved league focus when that league is no longer imported', async () => {
    const user = userEvent.setup();
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ entries: [], memory: [], leagueId: 5 }));
    const api = mockFetch([META_ROUTE, TWO_LEAGUES, { method: 'POST', path: CHAT, reply: json(chatReply()) }]);
    renderWithProviders(<AskPage />);

    expect(await screen.findByRole('combobox', { name: 'League for this chat' })).toHaveValue('');
    await user.type(composer(), 'Who?{Enter}');

    await waitFor(() => expect(api.callsTo(CHAT, 'POST')).toHaveLength(1));
    expect(lastBody(api)).not.toHaveProperty('league_id');
  });

  it('locks the league picker once the chat has started, until New chat', async () => {
    const user = userEvent.setup();
    mockFetch([META_ROUTE, TWO_LEAGUES, { method: 'POST', path: CHAT, reply: json(chatReply()) }]);
    renderWithProviders(<AskPage />);

    const picker = await screen.findByRole('combobox', { name: 'League for this chat' });
    await user.type(composer(), 'Who?{Enter}');
    await screen.findByText('Start Bijan Robinson.');
    expect(picker).toBeDisabled();
    expect(screen.getByText('Start a new chat to change league')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'New chat' }));
    expect(picker).toBeEnabled();
  });
});
