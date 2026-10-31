import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { App } from '../../App';
import type { TradeRequest } from '../../api/types';
import { BIJAN, CHASE, KELCE, profile, tradeResult } from '../../test/fixtures';
import { json, mockFetch, type MockRoute } from '../../test/mockFetch';
import { META_ROUTE, setHash } from '../../test/render';
import { render } from '@testing-library/react';

const SEARCH_ROUTE: MockRoute = {
  path: '/api/players/search',
  reply: ({ url }) => {
    const q = (url.searchParams.get('q') ?? '').toLowerCase();
    return json({ results: [BIJAN, CHASE, KELCE].filter((p) => p.name.toLowerCase().includes(q)) });
  },
};

function evaluateRoute(): MockRoute {
  return {
    method: 'POST',
    path: '/api/trade/evaluate',
    reply: ({ body }) => {
      const request = body as TradeRequest;
      if (request.get.includes(KELCE.id)) {
        return json(
          tradeResult({
            verdict: 'fair',
            percent_difference: 0.01,
            summary: 'This one is close to even.',
            balance_suggestions: [],
          }),
        );
      }
      return json(tradeResult());
    },
  };
}

function renderTrade(hash = '#/trade') {
  setHash(hash);
  return render(<App />);
}

function evaluateBodies(api: ReturnType<typeof mockFetch>): TradeRequest[] {
  return api.callsTo('/api/trade/evaluate', 'POST').map((call) => call.body as TradeRequest);
}

describe('TradePage', () => {
  it('searches, adds players, evaluates, and applies a balance suggestion', async () => {
    const user = userEvent.setup();
    const api = mockFetch([META_ROUTE, SEARCH_ROUTE, evaluateRoute()]);
    renderTrade();

    expect(screen.getByText('Add at least one player to each side.')).toBeInTheDocument();

    await user.type(screen.getByRole('combobox', { name: 'Add a player you give' }), 'Bij');
    await user.click(await screen.findByRole('option', { name: /Bijan Robinson/ }));
    expect(window.location.hash).toBe('#/trade?give=1');
    const giveList = screen.getByRole('list', { name: 'You give: 1 player' });
    expect(within(giveList).getByRole('link', { name: 'Bijan Robinson' })).toBeInTheDocument();

    const getBox = screen.getByRole('combobox', { name: 'Add a player you get' });
    await user.type(getBox, 'Chase');
    await screen.findByRole('option', { name: /Ja'Marr Chase/ });
    await user.keyboard('{ArrowDown}');
    expect(getBox).toHaveAttribute('aria-activedescendant');
    await user.keyboard('{Enter}');
    expect(window.location.hash).toBe('#/trade?give=1&get=2');

    expect(await screen.findByText('Slight loss for you')).toBeInTheDocument();
    expect(screen.getByText(tradeResult().summary)).toBeInTheDocument();
    expect(screen.getByRole('meter', { name: 'Trade balance' })).toHaveAttribute('aria-valuenow', '-10');
    expect(evaluateBodies(api)[0]).toEqual({
      give: [1],
      get: [2],
      settings: { scoring: 'ppr', teams: 12, superflex: false, te_premium: 0 },
    });
    expect(screen.getByText(/Package discount/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /Ask for: Travis Kelce/ }));
    expect(window.location.hash).toBe('#/trade?give=1&get=2,3');
    expect(await screen.findByText('Fair trade')).toBeInTheDocument();
    expect(evaluateBodies(api).at(-1)).toMatchObject({ give: [1], get: [2, 3] });
    expect(screen.getByRole('list', { name: 'You get: 2 players' })).toBeInTheDocument();
  });

  it('adds the first match when Enter is pressed without a highlight', async () => {
    const user = userEvent.setup();
    mockFetch([META_ROUTE, SEARCH_ROUTE, evaluateRoute()]);
    renderTrade();

    await user.type(screen.getByRole('combobox', { name: 'Add a player you give' }), 'Bij');
    await screen.findByRole('option', { name: /Bijan Robinson/ });
    await user.keyboard('{Enter}');

    expect(window.location.hash).toBe('#/trade?give=1');
  });

  it('hydrates a shared link, then swaps and clears sides', async () => {
    const user = userEvent.setup();
    mockFetch([META_ROUTE, evaluateRoute()]);
    renderTrade('#/trade?give=1&get=2');

    expect(await screen.findByText('Slight loss for you')).toBeInTheDocument();
    const giveList = screen.getByRole('list', { name: 'You give: 1 player' });
    expect(within(giveList).getByRole('link', { name: 'Bijan Robinson' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /Swap sides/ }));
    expect(window.location.hash).toBe('#/trade?give=2&get=1');

    await user.click(screen.getByRole('button', { name: /Clear/ }));
    expect(window.location.hash).toBe('#/trade');
    expect(await screen.findByText('Add at least one player to each side.')).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Add a player you give' })).toHaveFocus();
  });

  it('resolves names for a one-sided shared link and removes chips', async () => {
    const user = userEvent.setup();
    const api = mockFetch([META_ROUTE, { path: '/api/players/1', reply: json(profile()) }]);
    renderTrade('#/trade?give=1');

    const removeButton = await screen.findByRole('button', { name: 'Remove Bijan Robinson from You give' });
    expect(api.callsTo('/api/trade/evaluate', 'POST')).toHaveLength(0);
    await user.click(removeButton);
    expect(window.location.hash).toBe('#/trade');
    expect(screen.getByRole('combobox', { name: 'Add a player you give' })).toHaveFocus();
    expect(screen.getAllByText('No players yet. Search above to add one.')).toHaveLength(2);
  });

  it('shows the backend validation message when evaluation fails and still resolves names', async () => {
    mockFetch([
      META_ROUTE,
      { path: '/api/players/1', reply: json(profile()) },
      {
        method: 'POST',
        path: '/api/trade/evaluate',
        reply: json({ detail: [{ loc: ['body', 'get'], msg: 'Unknown player id 999' }] }, 422),
      },
    ]);
    renderTrade('#/trade?give=1&get=999');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent("Couldn't evaluate this trade");
    expect(alert).toHaveTextContent('get: Unknown player id 999');
    await waitFor(() => expect(screen.getByText('Player #999')).toBeInTheDocument());
    expect(await screen.findByRole('link', { name: 'Bijan Robinson' })).toBeInTheDocument();
    expect(screen.queryByText('adj.')).not.toBeInTheDocument();
  });

  it('closes the listbox with Escape and clears with a second Escape', async () => {
    const user = userEvent.setup();
    mockFetch([META_ROUTE, SEARCH_ROUTE]);
    renderTrade();
    const box = screen.getByRole('combobox', { name: 'Add a player you give' });
    await user.type(box, 'Tra');
    await screen.findByRole('option', { name: /Travis Kelce/ });
    expect(box).toHaveAttribute('aria-expanded', 'true');
    await user.keyboard('{Escape}');
    expect(box).toHaveAttribute('aria-expanded', 'false');
    await user.keyboard('{Escape}');
    expect(box).toHaveValue('');
  });
});

describe('TradePage league scoring', () => {
  const LEAGUES: MockRoute = { path: '/api/my-team/leagues', reply: json({ leagues: [{ id: 4, name: 'Family League' }] }) };

  it('scores the trade on a picked league and shows both lineups', async () => {
    const user = userEvent.setup();
    const lineup = { your_gain: 6.5, their_gain: 2.1, their_team: 'Rival', their_drop: null, your_add: null, note: null };
    const api = mockFetch([META_ROUTE, LEAGUES, {
      method: 'POST', path: '/api/trade/evaluate', reply: json(tradeResult({ lineup, market_percent_difference: 0.02 })),
    }]);
    renderTrade(`#/trade?give=${BIJAN.id}&get=${CHASE.id}`);
    await user.selectOptions(await screen.findByRole('combobox', { name: 'Score on' }), '4');
    await waitFor(() => expect(evaluateBodies(api).at(-1)?.league_id).toBe(4));
    expect(window.location.hash).toContain('league=4');
    expect(await screen.findByText(/Both lineups improve/)).toBeInTheDocument();
    expect(screen.getByText(/Expert consensus also sees this as about even/)).toBeInTheDocument();
    await user.selectOptions(screen.getByRole('combobox', { name: 'Score on' }), '');
    await waitFor(() => expect(evaluateBodies(api).at(-1)?.league_id).toBeUndefined());
  });

  it('keeps an unknown league from a link visible so it can be cleared, and shows why gains are missing', async () => {
    const lineup = { your_gain: null, their_gain: null, their_team: null, note: 'Some players you give aren’t on your roster.' };
    mockFetch([META_ROUTE, LEAGUES, { method: 'POST', path: '/api/trade/evaluate', reply: json(tradeResult({ lineup })) }]);
    renderTrade(`#/trade?give=${BIJAN.id}&get=${CHASE.id}&league=9`);
    expect(await screen.findByRole('option', { name: 'League #9 (not imported)' })).toBeInTheDocument();
    expect(await screen.findByText('Some players you give aren’t on your roster.')).toBeInTheDocument();
  });
});
