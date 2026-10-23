import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { SETTINGS_STORAGE_KEY } from '../../state/leagueSettings';
import { integrations } from '../../test/fixtures';
import { json, mockFetch, type MockRoute } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { SettingsPage } from './SettingsPage';
import { CODE_NOT_FOUND_MESSAGE } from './YahooConnect';
import { importSummary } from './YahooLeagues';

const AUTH_URL = 'https://api.login.yahoo.com/oauth2/request_auth?client_id=abc';

function yahooRoutes(): { routes: MockRoute[]; isConnected: () => boolean } {
  let connected = false;
  const routes: MockRoute[] = [
    META_ROUTE,
    { path: '/api/integrations/status', reply: () => json(integrations({ yahoo_connected: connected, llm: false })) },
    { path: '/api/integrations/yahoo/auth-url', reply: json({ url: AUTH_URL }) },
    {
      method: 'POST',
      path: '/api/integrations/yahoo/exchange',
      reply: ({ body }) => {
        if (!(body as { code: string }).code.includes('code=abc123')) return json({ detail: 'Invalid code' }, 400);
        connected = true;
        return json({ connected: true });
      },
    },
    {
      path: '/api/integrations/yahoo/leagues',
      reply: json({ leagues: [{ league_key: '449.l.777', name: 'Dynasty Degens', season: 2025, num_teams: 12, scoring_type: 'head' }] }),
    },
    {
      method: 'POST',
      path: '/api/integrations/yahoo/leagues/449.l.777/import',
      reply: json({ league_id: 3, teams: 12, roster_spots: 180, unmatched_players: ['Some Rookie'] }),
    },
  ];
  return { routes, isConnected: () => connected };
}

describe('SettingsPage integrations', () => {
  it('shows key instructions and runs the Yahoo connect + import flow', async () => {
    const user = userEvent.setup();
    const openSpy = vi.fn();
    vi.stubGlobal('open', openSpy);
    const { routes } = yahooRoutes();
    const api = mockFetch(routes);
    renderWithProviders(<SettingsPage />);

    expect(await screen.findByText('FANTASYPROS_API_KEY')).toBeInTheDocument();
    expect(screen.getByText('GROQ_API_KEY')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /Open Yahoo authorization/ }));
    expect(openSpy).toHaveBeenCalledWith(AUTH_URL, '_blank', 'noopener,noreferrer');
    expect(await screen.findByRole('link', { name: 'open Yahoo authorization' })).toHaveAttribute('href', AUTH_URL);

    const field = screen.getByRole('textbox', { name: /Paste the redirected URL or the code/ });
    await user.type(field, 'https://localhost/callback');
    await user.click(screen.getByRole('button', { name: 'Connect' }));
    expect(screen.getByText(CODE_NOT_FOUND_MESSAGE)).toBeInTheDocument();

    await user.clear(field);
    await user.type(field, 'https://localhost/callback?code=abc123&state=xyz');
    await user.click(screen.getByRole('button', { name: 'Connect' }));
    // The whole URL goes to the backend so it can verify the `state` security check.
    expect(api.callsTo('/api/integrations/yahoo/exchange', 'POST')[0].body).toEqual({
      code: 'https://localhost/callback?code=abc123&state=xyz',
    });

    const league = await screen.findByText('Dynasty Degens');
    const row = league.closest('li') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: 'Import Dynasty Degens' }));
    expect(await within(row).findByText(/Imported as league #3: 12 teams, 180 roster spots\./)).toBeInTheDocument();
    expect(within(row).getByText(/Some Rookie/)).toBeInTheDocument();
  });

  it('surfaces exchange failures from the backend', async () => {
    const user = userEvent.setup();
    mockFetch(yahooRoutes().routes);
    renderWithProviders(<SettingsPage />);
    const field = await screen.findByRole('textbox', { name: /Paste the redirected URL or the code/ });
    await user.type(field, 'wrongcode');
    await user.click(screen.getByRole('button', { name: 'Connect' }));
    expect(await screen.findByText('Connection failed: Invalid code')).toBeInTheDocument();
  });

  it('reports configured integrations and missing Yahoo credentials', async () => {
    mockFetch([
      META_ROUTE,
      { path: '/api/integrations/status', reply: json(integrations({ fantasypros: true, yahoo_configured: false })) },
    ]);
    renderWithProviders(<SettingsPage />);
    expect(await screen.findByText('Expert consensus rankings come from the FantasyPros API.')).toBeInTheDocument();
    expect(screen.getByText('Ask AI is ready.')).toBeInTheDocument();
    expect(screen.getByText('YAHOO_CLIENT_ID')).toBeInTheDocument();
  });
});

describe('SettingsPage league form', () => {
  it('persists scoring, teams, superflex and TE premium, validating input', async () => {
    const user = userEvent.setup();
    mockFetch([META_ROUTE, { path: '/api/integrations/status', reply: json(integrations({ yahoo_configured: false })) }]);
    renderWithProviders(<SettingsPage />);

    await user.click(screen.getByRole('radio', { name: /Half PPR/ }));
    await user.selectOptions(screen.getByRole('combobox', { name: 'Teams' }), '10');
    await user.click(screen.getByRole('checkbox', { name: /Superflex/ }));
    const te = screen.getByRole('spinbutton', { name: /TE premium/ });
    await user.clear(te);
    await user.type(te, '0.5');

    const stored = JSON.parse(window.localStorage.getItem(SETTINGS_STORAGE_KEY) ?? '{}');
    expect(stored).toEqual({ scoring: 'half', teams: 10, superflex: true, te_premium: 0.5 });

    await user.clear(te);
    await user.type(te, '3');
    expect(screen.getByText('Enter a number from 0 to 1.')).toBeInTheDocument();
    expect(JSON.parse(window.localStorage.getItem(SETTINGS_STORAGE_KEY) ?? '{}').te_premium).toBe(0.5);

    await user.click(screen.getByRole('button', { name: 'Reset to defaults' }));
    expect(screen.getByRole('radio', { name: /Full PPR/ })).toBeChecked();
    expect(screen.getByRole('spinbutton', { name: /TE premium/ })).toHaveValue(0);
  });

  it('changes the theme preference', async () => {
    const user = userEvent.setup();
    mockFetch([META_ROUTE, { path: '/api/integrations/status', reply: json(integrations()) }]);
    renderWithProviders(<SettingsPage />);
    await user.selectOptions(screen.getByRole('combobox', { name: 'Theme' }), 'light');
    expect(document.documentElement.dataset.theme).toBe('light');
  });
});

describe('importSummary', () => {
  it('reports a clean import', () => {
    expect(importSummary({ league_id: 1, teams: 1, roster_spots: 15, unmatched_players: [] })).toBe(
      'Imported as league #1: 1 team, 15 roster spots. Every player was matched.',
    );
  });
});
