import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { ReplayPendingOffer, ReplaySummary, ReplayTrades, ReplayVerdict } from '../../api/types';
import { json, mockFetch, type MockReply, type MockRequest, type MockRoute } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import {
  actionResult, COLLINS, ETIENNE, GAME_ID, MAHOMES, MCCAFFREY, MOSTERT, summary, team, teamRef, USER_TEAM_ID, useTestHub, viewProps,
} from './fixtures';
import type { ReplayViewProps } from './shared';
import { TradesView } from './TradesView';

const PATH = `/api/replay/games/${GAME_ID}`;
const TRADES_PATH = `${PATH}/trades`;
const EVALUATE_PATH = `${TRADES_PATH}/evaluate`;
const respondPath = (id: number) => `${TRADES_PATH}/offers/${id}/respond`;
const BRIGADE = teamRef(2);
const BROKE = 'It would leave Your Team without a full starting lineup.';

function verdict(overrides: Partial<ReplayVerdict> = {}): ReplayVerdict {
  return {
    accepted: true, reasons: [], fair_label: 'slight_win', value_give: 31.5, value_get: 35, percent: 0.1, your_gain: 3.2, their_gain: 1.1,
    give: [{ player: ETIENNE, value: 31.5 }], get: [{ player: MOSTERT, value: 35 }], your_drops: [], their_drops: [], suggestion: null,
    ...overrides,
  };
}

function offer(overrides: Partial<ReplayPendingOffer> = {}): ReplayPendingOffer {
  return { id: 61, week: 5, status: 'pending', from_team: BRIGADE, to_team: teamRef(USER_TEAM_ID), give: [MOSTERT], get: [ETIENNE],
    verdict: verdict(), note: null, counter_to: null, message: null, ...overrides };
}

function trades(overrides: Partial<ReplayTrades> = {}): ReplayTrades {
  return { open: true, deadline_week: 11, max_players: 3, offers: [], results: [], shops: [], ...overrides };
}

const answered = (accepted: boolean, expired = false, message: string | null = null) =>
  json({ ...actionResult(), accepted, expired, message, verdict: null });

function renderTrades(data: ReplayTrades | (() => MockReply | Promise<MockReply>), routes: MockRoute[] = [], props: Partial<ReplayViewProps> = {}) {
  const api = mockFetch([META_ROUTE, ...routes, { path: TRADES_PATH, reply: typeof data === 'function' ? data : json(data) },
    { path: `${PATH}/team`, reply: json(team()) }]);
  renderWithProviders(<TradesView {...viewProps(props)} />);
  return { api, user: userEvent.setup() };
}

function Hub() {
  return <TradesView {...viewProps(useTestHub())} />;
}

describe('TradeOffers: accept only on a yes for the current drops', () => {
  const full: ReplaySummary = summary({ settings: { ...summary().settings, roster_size: 11 } });
  const twoForOne = offer({ give: [MOSTERT, COLLINS], verdict: null, note: 'Choose exactly 1 to drop.' });
  /** Dropping McCaffrey breaks the lineup; dropping Mahomes works. */
  const evaluate = ({ body }: MockRequest): MockReply => {
    const drops = (body as { drops: number[] }).drops;
    return drops.includes(MCCAFFREY.id)
      ? json({ verdict: verdict({ accepted: false, reasons: [BROKE], your_drops: [MCCAFFREY] }) })
      : json({ verdict: verdict({ your_drops: [MAHOMES] }) });
  };

  it('keeps Accept disabled when the chosen drop is refused, and enables it for one that works', async () => {
    const { api, user } = renderTrades(trades({ offers: [twoForOne] }), [
      { method: 'POST', path: EVALUATE_PATH, reply: evaluate },
      { method: 'POST', path: respondPath(61), reply: answered(true) },
    ], { game: full });
    const card = await screen.findByRole('listitem', { name: 'Offer from Blitz Brigade' });
    const accept = within(card).getByRole('button', { name: 'Accept offer from Blitz Brigade' });

    await user.click(within(card).getByRole('checkbox', { name: /Drop Christian McCaffrey/ }));
    expect(await within(card).findByText(BROKE)).toBeInTheDocument();
    expect(accept).toBeDisabled();
    await user.click(accept);
    expect(api.callsTo(respondPath(61), 'POST')).toEqual([]);

    await user.click(within(card).getByRole('checkbox', { name: /Drop Christian McCaffrey/ }));
    await user.click(within(card).getByRole('checkbox', { name: /Drop Patrick Mahomes/ }));
    await waitFor(() => expect(accept).toBeEnabled());
    await user.click(accept);
    await waitFor(() => expect(api.callsTo(respondPath(61), 'POST')[0]?.body).toEqual({ accept: true, drops: [MAHOMES.id] }));
  });

  it('keeps Accept disabled while the chosen drop is being checked', async () => {
    renderTrades(trades({ offers: [twoForOne] }), [
      { method: 'POST', path: EVALUATE_PATH, reply: () => new Promise<MockReply>(() => undefined) },
    ], { game: full });
    const card = await screen.findByRole('listitem', { name: 'Offer from Blitz Brigade' });
    await userEvent.setup().click(within(card).getByRole('checkbox', { name: /Drop Patrick Mahomes/ }));
    expect(await within(card).findByText('Checking that choice…')).toBeInTheDocument();
    expect(within(card).getByRole('button', { name: 'Accept offer from Blitz Brigade' })).toBeDisabled();
  });

  it('only lets an offer be declined when its own verdict says no', async () => {
    renderTrades(trades({ offers: [offer({ verdict: verdict({ accepted: false, reasons: [BROKE] }) })] }));
    const card = await screen.findByRole('listitem', { name: 'Offer from Blitz Brigade' });
    expect(within(card).getByRole('button', { name: 'Accept offer from Blitz Brigade' })).toBeDisabled();
    expect(within(card).getByRole('button', { name: 'Decline offer from Blitz Brigade' })).toBeEnabled();
  });
});

describe('TradesView: announcing the answer', () => {
  it('has one empty live region before anything is answered', async () => {
    renderTrades(trades({ open: false, offers: [offer()] }));
    await screen.findByRole('listitem', { name: 'Offer from Blitz Brigade' });
    const regions = screen.getAllByRole('status');
    expect(regions).toHaveLength(1);
    expect(regions[0]).toBeEmptyDOMElement();
  });

  it.each([
    ['Accept', answered(true), 'Trade executed with Blitz Brigade.'],
    ['Decline', answered(false), "You declined Blitz Brigade's offer."],
    ['Accept', answered(false, true, 'That offer no longer stands.'), 'That offer no longer stands.'],
  ])('moves focus to the result after %s (%#)', async (button, reply, text) => {
    const { user } = renderTrades(trades({ offers: [offer()] }), [{ method: 'POST', path: respondPath(61), reply }]);
    await user.click(await screen.findByRole('button', { name: `${button} offer from Blitz Brigade` }));
    const result = await screen.findByText(text);
    expect(result).toHaveAttribute('role', 'status');
    await waitFor(() => expect(result).toHaveFocus());
  });
});

describe('TradesView: while the list reloads', () => {
  it('disables the other offers until the reloaded list arrives', async () => {
    const both = trades({ offers: [offer(), offer({ id: 62, from_team: teamRef(1) })] });
    let calls = 0;
    let finish: (() => void) | undefined;
    const tradesReply = () => {
      calls += 1;
      return calls === 1 ? json(both) : new Promise<MockReply>((resolve) => { finish = () => resolve(json(trades())); });
    };
    mockFetch([META_ROUTE, { method: 'POST', path: respondPath(61), reply: answered(false) },
      { path: TRADES_PATH, reply: tradesReply }, { path: `${PATH}/team`, reply: json(team()) }]);
    renderWithProviders(<Hub />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Decline offer from Blitz Brigade' }));
    await waitFor(() => expect(calls).toBe(2));
    expect(screen.getByRole('button', { name: 'Accept offer from Gridiron Ghosts' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Decline offer from Gridiron Ghosts' })).toBeDisabled();
    finish?.();
    expect(await screen.findByText('No offers are waiting for your answer.')).toBeInTheDocument();
  });
});

describe('TradeVerdictView: the headline is trade value, the lineup is called out', () => {
  it('warns about a big lineup loss next to a mild value headline and explains totals that are not the rows', async () => {
    const hurt = verdict({ fair_label: 'slight_loss', percent: -0.06, value_give: 801.7, value_get: 754, your_gain: -60.7,
      give: [{ player: ETIENNE, value: 744.1 }], get: [{ player: MOSTERT, value: 754 }] });
    renderTrades(trades({ offers: [offer({ verdict: hurt })] }));
    const card = await screen.findByRole('listitem', { name: 'Offer from Blitz Brigade' });

    expect(within(card).getByText(/on trade value\. You give 802 value, get 754\./)).toBeInTheDocument();
    expect(within(card).getByText(/count the best player in the deal a little extra/)).toBeInTheDocument();
    expect(within(card).getByText(/this costs your best lineup 60\.7 points over the rest of the season/)).toBeInTheDocument();
  });

  it('stays quiet when the lineup barely moves and the totals are the rows', async () => {
    renderTrades(trades({ offers: [offer()] }));
    const card = await screen.findByRole('listitem', { name: 'Offer from Blitz Brigade' });
    expect(within(card).queryByText(/costs your best lineup/)).not.toBeInTheDocument();
    expect(within(card).queryByText(/a little extra/)).not.toBeInTheDocument();
  });
});
