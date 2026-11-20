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
  return { id, order, add: playerRow(add), drop: drop && playerRow(drop) };
}

function claims(results: ReplayClaim[] = []): ReplayClaims {
  return { phase: 'waivers', can_claim: true, priority: 4, waiver_order: TEAMS, results };
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
