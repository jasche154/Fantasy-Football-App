import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { ReplayClaim, ReplayClaims, ReplayPlayerRef, ReplaySummary } from '../../api/types';
import { json, mockFetch, type MockReply, type MockRoute } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import {
  actionResult, COLLINS, GAME_ID, HILL, MOSTERT, nextAction, playerRow, summary, team, TEAMS, useTestHub, viewProps, wireRow,
} from './fixtures';
import { PlayersView } from './PlayersView';

const GAME_PATH = `/api/replay/games/${GAME_ID}`;
const PLAYERS = `${GAME_PATH}/players`;
const CLAIMS = `${GAME_PATH}/claims`;
const TEAM = `${GAME_PATH}/team`;
const REORDER = `${CLAIMS}/reorder`;

const WAIVERS = summary({ phase: 'waivers', next_action: nextAction('wednesday'), pending: { claims: 2, offers: 0 } });

function claim(id: number, order: number, add: ReplayPlayerRef, drop: ReplayPlayerRef | null = null): ReplayClaim {
  return { id, order, add: playerRow(add), drop: drop && playerRow(drop), bid: 0 };
}

function claims(results: ReplayClaim[] = []): ReplayClaims {
  return { phase: 'waivers', can_claim: true, priority: 4, waiver_order: TEAMS, results, faab: null };
}

function Hub({ game }: { game: ReplaySummary }) {
  return <PlayersView {...viewProps({ game, ...useTestHub() })} />;
}

/** GET /claims answers each call with the next reply (the last one repeats). */
function claimsSequence(...replies: (ReplayClaims | (() => Promise<MockReply>))[]): MockRoute {
  let call = 0;
  return {
    path: CLAIMS,
    reply: () => {
      const next = replies[Math.min(call++, replies.length - 1)];
      return typeof next === 'function' ? next() : json(next);
    },
  };
}

function renderPlayers(game: ReplaySummary, routes: MockRoute[]) {
  const api = mockFetch([META_ROUTE, ...routes,
    { path: PLAYERS, reply: json({ total: 2, limit: 25, offset: 0, week: 5, results: [wireRow(MOSTERT, { status: 'waivers' }), wireRow(HILL, { status: 'waivers' })] }) },
    { path: TEAM, reply: json(team()) },
    { method: 'POST', path: REORDER, reply: json(actionResult()) },
    { method: 'POST', path: CLAIMS, reply: json(actionResult()) }]);
  renderWithProviders(<Hub game={game} />);
  return { api, user: userEvent.setup() };
}

async function ready(name: string) {
  const button = await screen.findByRole('button', { name });
  await waitFor(() => expect(button).toBeEnabled());
  return button;
}

function claimNames() {
  return within(screen.getByRole('list', { name: 'Your pending claims' })).getAllByRole('listitem')
    .map((item) => item.textContent ?? '');
}

describe('ClaimsPanel', () => {
  it('keeps the claim buttons disabled until the reordered claims have reloaded', async () => {
    let finish: (reply: MockReply) => void = () => undefined;
    const held = () => new Promise<MockReply>((resolve) => { finish = resolve; });
    const { user } = renderPlayers(WAIVERS, [claimsSequence(claims([claim(31, 1, MOSTERT), claim(32, 2, COLLINS)]), held)]);
    await user.click(await ready('Down, claim for Raheem Mostert'));

    expect(await screen.findByText('Claim order updated.')).toBeInTheDocument();
    for (const name of ['Up, claim for Nico Collins', 'Down, claim for Raheem Mostert', 'Cancel claim for Nico Collins']) {
      expect(screen.getByRole('button', { name })).toBeDisabled();
    }
    finish(json(claims([claim(32, 1, COLLINS), claim(31, 2, MOSTERT)])));
    expect(await ready('Up, claim for Raheem Mostert')).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Down, claim for Nico Collins' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Cancel claim for Nico Collins' })).toBeEnabled();
  });

  it('keeps keyboard focus on a claim moved to the bottom', async () => {
    const { api, user } = renderPlayers(WAIVERS, [claimsSequence(
      claims([claim(31, 1, MOSTERT), claim(32, 2, COLLINS)]),
      claims([claim(32, 1, COLLINS), claim(31, 2, MOSTERT)]),
    )]);
    await user.click(await ready('Down, claim for Raheem Mostert'));
    await waitFor(() => expect(claimNames()[0]).toMatch(/Nico Collins/));
    expect(api.callsTo(REORDER, 'POST')[0].body).toEqual({ ids: [32, 31] });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Up, claim for Raheem Mostert' })).toHaveFocus());
  });

  it('keeps keyboard focus on the same button when a claim moves down the middle', async () => {
    const { user } = renderPlayers(WAIVERS, [claimsSequence(
      claims([claim(31, 1, MOSTERT), claim(32, 2, COLLINS), claim(33, 3, HILL)]),
      claims([claim(32, 1, COLLINS), claim(31, 2, MOSTERT), claim(33, 3, HILL)]),
    )]);
    await user.click(await ready('Down, claim for Raheem Mostert'));
    await waitFor(() => expect(claimNames()[0]).toMatch(/Nico Collins/));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Down, claim for Raheem Mostert' })).toHaveFocus());
  });
});

describe('ClaimsPanel FAAB', () => {
  const FAAB_SETTINGS = { ...summary().settings, waiver_mode: 'faab' as const };
  /** The week is open, nothing pending: only a FAAB league still loads the claims. */
  const FAAB_OPEN = summary({ settings: FAAB_SETTINGS });
  const FAAB_WAIVERS = summary({ ...WAIVERS, settings: FAAB_SETTINGS });
  const BID = `${CLAIMS}/31/bid`;

  function faabClaims(results: ReplayClaim[] = []): ReplayClaims {
    const order = [TEAMS[3], TEAMS[0], TEAMS[1]];
    return { ...claims(results), waiver_order: order, priority: 1,
      faab: { budget: 100, left: 64, teams: order.map((team, index) => ({ team, left: [64, 100, 12][index] })) } };
  }

  it('shows the budget left and every team\'s budget even outside the waiver phase', async () => {
    const { api } = renderPlayers(FAAB_OPEN, [claimsSequence({ ...faabClaims(), phase: 'open', can_claim: false })]);
    const panel = within(await screen.findByRole('region', { name: 'Waiver claims' }));
    expect(await panel.findByText('You have $64 of $100 left.')).toBeInTheDocument();
    expect(panel.getByText(/each player goes to the highest bid; a tie goes to the team higher in the waiver order/)).toBeInTheDocument();
    expect(panel.getByText('Claims open after the next week is played.')).toBeInTheDocument();     // no Claim button now
    expect(panel.queryByText(/Press Claim/)).not.toBeInTheDocument();
    const budgets = within(panel.getByRole('list', { name: 'Waiver order and budgets left' })).getAllByRole('listitem').map((item) => item.textContent);
    expect(budgets).toEqual(['Your Team (you): $64', 'Gridiron Ghosts: $100', 'Blitz Brigade: $12']);
    expect(api.callsTo(CLAIMS)).toHaveLength(1);
  });

  it('edits a pending claim\'s bid through the bid route', async () => {
    const { api, user } = renderPlayers(FAAB_WAIVERS, [{ method: 'POST', path: BID, reply: json(actionResult()) },
      claimsSequence(faabClaims([{ ...claim(31, 1, MOSTERT), bid: 5 }]))]);
    const input = await screen.findByRole('spinbutton', { name: 'Bid ($) for Raheem Mostert' });
    expect(input).toHaveValue(5);
    const save = screen.getByRole('button', { name: 'Save bid for Raheem Mostert' });
    expect(save).toBeDisabled();                                         // unchanged
    await user.clear(input);
    await user.type(input, '65');
    expect(save).toBeDisabled();                                         // over the $64 left
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(input).toHaveAccessibleDescription('Whole dollars, $0 to $64.');
    await user.clear(input);
    await user.type(input, '22');
    expect(input).toHaveAttribute('aria-invalid', 'false');
    expect(input).toHaveAccessibleDescription('Not saved: the claim still bids $5.');
    await user.click(await ready('Save bid for Raheem Mostert'));
    await waitFor(() => expect(api.callsTo(BID, 'POST')[0]?.body).toEqual({ bid: 22 }));
    expect(await screen.findByText('Bid for Raheem Mostert is now $22.')).toBeInTheDocument();
  });

  it('saves a bid edit on Enter', async () => {
    const { api, user } = renderPlayers(FAAB_WAIVERS, [{ method: 'POST', path: BID, reply: json(actionResult()) },
      claimsSequence(faabClaims([{ ...claim(31, 1, MOSTERT), bid: 5 }]))]);
    const input = await screen.findByRole('spinbutton', { name: 'Bid ($) for Raheem Mostert' });
    await ready('Cancel claim for Raheem Mostert');
    await user.clear(input);
    await user.type(input, '30{Enter}');
    await waitFor(() => expect(api.callsTo(BID, 'POST')[0]?.body).toEqual({ bid: 30 }));
  });

  it('shows only the budgets once the season is over', async () => {
    const over = summary({ phase: 'complete', next_action: nextAction('done'), settings: FAAB_SETTINGS });
    renderPlayers(over, [claimsSequence({ ...faabClaims(), phase: 'complete', can_claim: false })]);
    const panel = within(await screen.findByRole('region', { name: 'Waiver claims' }));
    expect(await panel.findByText('You have $64 of $100 left.')).toBeInTheDocument();
    expect(panel.getByText('The season is over: no more claims.')).toBeInTheDocument();
    expect(panel.queryByText(/Sim to Wednesday/)).not.toBeInTheDocument();
  });

  it('asks for a whole-dollar bid in the claim dialog and sends it', async () => {
    const { api, user } = renderPlayers(FAAB_WAIVERS, [claimsSequence(faabClaims())]);
    await user.click(await ready('Claim Raheem Mostert'));
    const dialog = within(await screen.findByRole('dialog', { name: 'Claim Raheem Mostert' }));
    const bid = await dialog.findByRole('spinbutton', { name: 'Bid ($)' });
    expect(bid).toHaveValue(0);
    expect(bid).toHaveAttribute('max', '64');
    expect(dialog.getByText(/you have \$64 left/)).toBeInTheDocument();
    await user.clear(bid);
    await user.type(bid, '2.5');
    expect(dialog.getByRole('button', { name: 'Submit claim' })).toBeDisabled();
    expect(bid).toHaveAttribute('aria-invalid', 'true');
    await user.clear(bid);
    await user.type(bid, '17');
    await user.click(dialog.getByRole('button', { name: 'Submit claim' }));
    await waitFor(() => expect(api.callsTo(CLAIMS, 'POST')[0]?.body).toEqual({ add: MOSTERT.id, bid: 17 }));
    expect(await screen.findByText('Claim for Raheem Mostert is in for $17. It is settled on “Sim to Wednesday”.')).toBeInTheDocument();
  });

  it('has no bid input and sends no bid outside FAAB', async () => {
    const { api, user } = renderPlayers(WAIVERS, [claimsSequence(claims())]);
    await user.click(await ready('Claim Raheem Mostert'));
    const dialog = within(await screen.findByRole('dialog', { name: 'Claim Raheem Mostert' }));
    await user.click(await dialog.findByRole('button', { name: 'Submit claim' }));
    expect(dialog.queryByRole('spinbutton')).not.toBeInTheDocument();
    await waitFor(() => expect(api.callsTo(CLAIMS, 'POST')[0]?.body).toEqual({ add: MOSTERT.id }));
    expect(screen.queryByText(/\$\d+ left/)).not.toBeInTheDocument();
  });
});

describe('PlayersView claim dialog', () => {
  const ROOM_FOR_ONE = summary({ ...WAIVERS, pending: { claims: 1, offers: 0 }, settings: { ...summary().settings, roster_size: 12 } });

  it('asks for a drop choice when earlier claims without a drop already fill the open spots', async () => {
    const { api, user } = renderPlayers(ROOM_FOR_ONE, [claimsSequence(claims([claim(31, 1, COLLINS)]))]);
    await user.click(await ready('Claim Raheem Mostert'));
    const dialog = within(await screen.findByRole('dialog', { name: 'Claim Raheem Mostert' }));
    const none = await dialog.findByRole('radio', { name: 'Don\'t drop anyone' });
    expect(dialog.getAllByRole('radio').filter((radio) => (radio as HTMLInputElement).checked)).toHaveLength(0);
    expect(dialog.getByRole('button', { name: 'Submit claim' })).toBeDisabled();
    expect(dialog.getByText(/You have 11 of 12 roster spots filled, but your earlier 1 claim without a drop would fill the open spot\. If that wins, this claim fails unless you pick a drop\./)).toBeInTheDocument();

    await user.click(none);
    await user.click(dialog.getByRole('button', { name: 'Submit claim' }));
    await waitFor(() => expect(api.callsTo(CLAIMS, 'POST')[0]?.body).toEqual({ add: MOSTERT.id }));
  });
});
