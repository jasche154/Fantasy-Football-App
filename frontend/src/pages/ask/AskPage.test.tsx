import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { integrations, meta } from '../../test/fixtures';
import { json, mockFetch } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { AskPage } from './AskPage';
import { summarizeArguments } from './toolCalls';

function composer(): HTMLElement {
  return screen.getByRole('textbox', { name: 'Ask a fantasy football question' });
}

describe('AskPage', () => {
  it('explains how to enable the assistant when the backend returns 503', async () => {
    const user = userEvent.setup();
    mockFetch([
      META_ROUTE,
      { method: 'POST', path: '/api/agent/chat', reply: json({ detail: 'No LLM API key configured' }, 503) },
    ]);
    renderWithProviders(<AskPage />);

    await user.type(composer(), 'Who should I start?{Enter}');

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('AI assistant not configured');
    expect(alert).toHaveTextContent('Add GROQ_API_KEY to backend/.env');
    expect(composer()).toHaveValue('Who should I start?');
    expect(screen.queryByText('Who should I start?', { selector: '.message__text' })).not.toBeInTheDocument();
  });

  it('sends the conversation and shows tool-call chips under the reply', async () => {
    const user = userEvent.setup();
    const api = mockFetch([
      META_ROUTE,
      {
        method: 'POST',
        path: '/api/agent/chat',
        reply: json({
          reply: 'Bijan Robinson is the top buy-low RB.',
          tool_calls: [
            { name: 'get_buy_low_candidates', arguments: { position: 'RB', limit: 5 }, ok: true },
            { name: 'get_player_profile', arguments: { player: 'Nobody' }, ok: false },
          ],
        }),
      },
    ]);
    renderWithProviders(<AskPage />);

    await user.click(screen.getByRole('button', { name: 'Who are the best buy-low RBs right now?' }));

    expect(await screen.findByText('Bijan Robinson is the top buy-low RB.')).toBeInTheDocument();
    expect(screen.getByText('Sources: 2 sources')).toBeInTheDocument();
    expect(screen.getByText('Buy-low candidates')).toHaveAttribute('title', 'get_buy_low_candidates');
    expect(screen.getByText('position: RB, limit: 5')).toBeInTheDocument();
    expect(screen.getByText('failed')).toBeInTheDocument();
    expect(api.callsTo('/api/agent/chat', 'POST')[0].body).toEqual({
      messages: [{ role: 'user', content: 'Who are the best buy-low RBs right now?' }],
      memory: [],
    });

    await user.click(screen.getByRole('button', { name: 'New chat' }));
    expect(screen.queryByText('Bijan Robinson is the top buy-low RB.')).not.toBeInTheDocument();
  });

  it('uses Shift+Enter for new lines and shows a generic error for other failures', async () => {
    const user = userEvent.setup();
    mockFetch([META_ROUTE, { method: 'POST', path: '/api/agent/chat', reply: json({ detail: 'Upstream timeout' }, 504) }]);
    renderWithProviders(<AskPage />);

    await user.type(composer(), 'line one{Shift>}{Enter}{/Shift}line two');
    expect(composer()).toHaveValue('line one\nline two');
    await user.click(screen.getByRole('button', { name: 'Send' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('The assistant couldn’t answer');
    expect(alert).toHaveTextContent('Upstream timeout');
  });

  it('discards a pending question on New chat instead of restoring it', async () => {
    const user = userEvent.setup();
    mockFetch([META_ROUTE, { method: 'POST', path: '/api/agent/chat', reply: () => new Promise(() => undefined) }]);
    renderWithProviders(<AskPage />);
    await user.type(composer(), 'Is Puka a buy?{Enter}');
    expect(await screen.findByText('Looking up the data…')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Send' }));
    await user.click(screen.getByRole('button', { name: 'New chat' }));
    expect(screen.queryByText('Looking up the data…')).not.toBeInTheDocument();
    expect(composer()).toHaveValue('');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('warns up front when meta reports no LLM key', async () => {
    mockFetch([{ path: '/api/meta', reply: json(meta({ integrations: integrations({ llm: false }) })) }]);
    renderWithProviders(<AskPage />);
    expect(await screen.findByRole('note')).toHaveTextContent('Add GROQ_API_KEY to backend/.env');
  });
});

describe('summarizeArguments', () => {
  it('summarises and truncates tool arguments', () => {
    expect(summarizeArguments({ give: [1, 2], nested: { a: 1 }, none: null })).toBe('give: 1, 2, nested: {"a":1}, none: —');
    expect(summarizeArguments({ text: 'x'.repeat(200) })).toHaveLength(80);
    expect(summarizeArguments({})).toBe('');
  });
  it('keeps remembered facts, shows them, and sends them with the next message', async () => {
    const user = userEvent.setup();
    const api = mockFetch([
      META_ROUTE,
      { method: 'POST', path: '/api/agent/chat',
        reply: json({ reply: 'Noted.', tool_calls: [{ name: 'remember', arguments: { fact: "I'm 5-1" }, ok: true }],
          memory: ["I'm 5-1"] }) },
    ]);
    renderWithProviders(<AskPage />);

    await user.type(composer(), "I'm 5-1{Enter}");
    const note = await screen.findByRole('region', { name: 'Keeping in mind' });
    expect(note).toHaveTextContent("I'm 5-1");

    await user.type(composer(), 'Who should I start?{Enter}');
    await screen.findAllByText('Noted.');
    expect(api.callsTo('/api/agent/chat', 'POST')[1].body).toMatchObject({ memory: ["I'm 5-1"] });

    await user.click(screen.getByRole('button', { name: 'New chat' }));
    expect(screen.queryByRole('region', { name: 'Keeping in mind' })).not.toBeInTheDocument();
  });
});
