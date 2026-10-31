import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { json, mockFetch, type MockRoute } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { AskPage } from './AskPage';
import { chatReply, league } from './askFixtures';
import { MAX_ASSISTANT_CHARS } from './useChat';

const CHAT = '/api/agent/chat';

function leaguesRoute(leagues = [league(1, 'Chen'), league(2, 'Tintin')]): MockRoute {
  return { path: '/api/my-team/leagues', reply: json({ leagues }) };
}

function composer(): HTMLElement {
  return screen.getByRole('textbox', { name: 'Ask a fantasy football question' });
}

describe('AskPage features', () => {
  it('offers starter questions for the user leagues and sends follow-up chips', async () => {
    const user = userEvent.setup();
    const api = mockFetch([META_ROUTE, leaguesRoute([league(1, 'Chen')]),
      { method: 'POST', path: CHAT, reply: json(chatReply({ suggestions: ['Any injury news on my starters?'] })) }]);
    renderWithProviders(<AskPage />);

    await user.click(await screen.findByRole('button', { name: 'Who should I start in week 9 in Chen?' }));
    const followUps = await screen.findByRole('region', { name: 'Ask next' });
    await user.click(within(followUps).getByRole('button', { name: 'Any injury news on my starters?' }));

    await waitFor(() => expect(api.callsTo(CHAT, 'POST')).toHaveLength(2));
    expect(api.callsTo(CHAT, 'POST')[1].body).toMatchObject({
      messages: [{ content: 'Who should I start in week 9 in Chen?' }, { role: 'assistant' },
        { role: 'user', content: 'Any injury news on my starters?' }],
    });
  });

  it('shows sources with the data week, an unverified note and a partial-answer note', async () => {
    const user = userEvent.setup();
    mockFetch([META_ROUTE, leaguesRoute([]), { method: 'POST', path: CHAT, reply: json(chatReply({
      tool_calls: [{ name: 'start_sit', arguments: { players: ['A', 'B'] }, ok: true }],
      unverified: ['Nacua', '18.4'], stop_reason: 'out_of_rounds' })) }]);
    renderWithProviders(<AskPage />);

    await user.type(composer(), 'A or B?{Enter}');
    expect(await screen.findByText('Sources: 1 source · week 9 data')).toBeInTheDocument();
    expect(screen.getByText('Start/sit comparison')).toBeInTheDocument();
    expect(screen.getByText(/Not matched to the app’s data: Nacua, 18.4/)).toBeInTheDocument();
    expect(screen.getByText(/Partial answer/)).toBeInTheDocument();
  });

  it('copies a reply to the clipboard', async () => {
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue(undefined);
    mockFetch([META_ROUTE, leaguesRoute([]), { method: 'POST', path: CHAT, reply: json(chatReply()) }]);
    renderWithProviders(<AskPage />);

    await user.type(composer(), 'Who?{Enter}');
    await user.click(await screen.findByRole('button', { name: 'Copy' }));
    expect(writeText).toHaveBeenCalledWith('Start Bijan Robinson.');
    expect(await screen.findByRole('button', { name: /Copied/ })).toBeInTheDocument();
  });

  it('focuses the chat on one league when there are two', async () => {
    const user = userEvent.setup();
    const api = mockFetch([META_ROUTE, leaguesRoute(), { method: 'POST', path: CHAT, reply: json(chatReply()) }]);
    renderWithProviders(<AskPage />);

    await user.selectOptions(await screen.findByRole('combobox', { name: 'League for this chat' }), 'Tintin');
    expect(screen.queryByRole('button', { name: /in Chen\?/ })).not.toBeInTheDocument();
    await user.type(composer(), 'Who should I start?{Enter}');

    await waitFor(() => expect(api.callsTo(CHAT, 'POST')).toHaveLength(1));
    expect(api.callsTo(CHAT, 'POST')[0].body).toMatchObject({ league_id: 2 });
  });

  it('keeps the chat after a reload and announces only new replies', async () => {
    const user = userEvent.setup();
    mockFetch([META_ROUTE, leaguesRoute([]), { method: 'POST', path: CHAT,
      reply: json(chatReply({ memory: ['I am 5-3'] })) }]);
    const first = renderWithProviders(<AskPage />);
    await user.type(composer(), 'Who?{Enter}');
    expect(await screen.findByText('Assistant replied: Start Bijan Robinson.')).toBeInTheDocument();
    first.unmount();

    renderWithProviders(<AskPage />);
    expect(screen.getByText('Start Bijan Robinson.')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Keeping in mind' })).toHaveTextContent('I am 5-3');
    expect(screen.queryByText(/Assistant replied/)).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'New chat' }));
    expect(screen.queryByText('Start Bijan Robinson.')).not.toBeInTheDocument();
  });

  it('trims a very long reply before sending it back as history', async () => {
    const user = userEvent.setup();
    const api = mockFetch([META_ROUTE, leaguesRoute([]),
      { method: 'POST', path: CHAT, reply: json(chatReply({ reply: 'x'.repeat(MAX_ASSISTANT_CHARS + 100) })) }]);
    renderWithProviders(<AskPage />);

    await user.type(composer(), 'Long?{Enter}');
    await screen.findByRole('button', { name: 'Copy' });
    await user.type(composer(), 'And?{Enter}');

    await waitFor(() => expect(api.callsTo(CHAT, 'POST')).toHaveLength(2));
    const body = api.callsTo(CHAT, 'POST')[1].body as { messages: { content: string }[] };
    expect(body.messages[1].content).toHaveLength(MAX_ASSISTANT_CHARS);
  });

  it('retries a rate-limited question once the wait is over', async () => {
    const user = userEvent.setup();
    let calls = 0;
    const api = mockFetch([META_ROUTE, leaguesRoute([]), { method: 'POST', path: CHAT, reply: () => {
      calls += 1;
      return calls === 1
        ? { status: 429, body: { detail: 'limit' }, headers: { 'Retry-After': '1' } }
        : json(chatReply());
    } }]);
    renderWithProviders(<AskPage />);

    await user.type(composer(), 'Who?{Enter}');
    expect(await screen.findByText('Free-tier limit reached')).toBeInTheDocument();
    await user.click(await screen.findByRole('button', { name: 'Try again' }, { timeout: 3000 }));

    expect(await screen.findByText('Start Bijan Robinson.')).toBeInTheDocument();
    expect(api.callsTo(CHAT, 'POST')[1].body).toMatchObject({ messages: [{ role: 'user', content: 'Who?' }] });
  });
});
