import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { ReplayPendingOffer, ReplaySummary, ReplayTrades, ReplayVerdict } from '../../api/types';
import { json, mockFetch, type MockReply, type MockRequest, type MockRoute } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import {
  actionResult, COLLINS, ETIENNE, GAME_ID, MCCAFFREY, MOSTERT, rosterRow, summary, tagged, team, teamRef, USER_TEAM_ID, viewProps,
} from './fixtures';
import type { ReplayViewProps } from './shared';
import { TradesView } from './TradesView';

const PATH = `/api/replay/games/${GAME_ID}`;
const TRADES_PATH = `${PATH}/trades`;
const EVALUATE_PATH = `${TRADES_PATH}/evaluate`;
const PROPOSE_PATH = `${TRADES_PATH}/propose`;
const RESPOND_PATH = `${TRADES_PATH}/offers/61/respond`;
/** The partner the builder starts on (the first CPU team) and the one that made the offer. */
const GHOSTS = teamRef(1);
const BRIGADE = teamRef(2);

function verdict(overrides: Partial<ReplayVerdict> = {}): ReplayVerdict {
  return {
    accepted: true, reasons: [], fair_label: 'slight_win', value_give: 31.5, value_get: 35, percent: 0.1, your_gain: 3.2, their_gain: 1.1,
    give: [{ player: ETIENNE, value: 31.5 }], get: [{ player: MOSTERT, value: 35 }], your_drops: [], their_drops: [], suggestion: null,
    ...overrides,
  };
}

/** Blitz Brigade offers Mostert for Etienne. */
function offer(overrides: Partial<ReplayPendingOffer> = {}): ReplayPendingOffer {
  return { id: 61, week: 5, status: 'pending', from_team: BRIGADE, to_team: teamRef(USER_TEAM_ID), give: [MOSTERT], get: [ETIENNE],
    verdict: verdict(), note: null, ...overrides };
}

function trades(overrides: Partial<ReplayTrades> = {}): ReplayTrades {
  return { open: true, deadline_week: 11, max_players: 3, out_discount: 0.8, offers: [], results: [], ...overrides };
}

/** GET team: the user's roster, or a CPU roster of Mostert and Collins (locked). */
function teamReply({ url }: MockRequest): MockReply {
  const teamId = Number(url.searchParams.get('team_id'));
  return json(teamId === USER_TEAM_ID ? team() : team({
    team: teamRef(teamId), results: [rosterRow(MOSTERT, 'RB', { value: 35 }), rosterRow(COLLINS, 'WR', { trade_locked: true })],
  }));
}

function renderTrades(data: ReplayTrades, routes: MockRoute[] = [], options: { props?: Partial<ReplayViewProps>; hash?: string } = {}) {
  const api = mockFetch([META_ROUTE, ...routes, { path: TRADES_PATH, reply: json(data) }, { path: `${PATH}/team`, reply: teamReply }]);
  renderWithProviders(<TradesView {...viewProps(options.props)} />, { hash: options.hash });
  return { api, user: userEvent.setup() };
}

async function tickBothSides(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('checkbox', { name: 'Give Travis Etienne' }));
  await user.click(await screen.findByRole('checkbox', { name: 'Get Raheem Mostert' }));
}

describe('TradesView offers', () => {
  it('shows an offer with its values, fairness and lineup gain, and the history with a status', async () => {
    const openPlayer = vi.fn();
    const past = { id: 50, week: 3, status: 'declined' as const, from_team: teamRef(USER_TEAM_ID), to_team: GHOSTS, give: [MCCAFFREY], get: [COLLINS] };
    const { user } = renderTrades(trades({ offers: [offer()], results: [past] }), [], { props: { openPlayer } });

    const card = await screen.findByRole('listitem', { name: 'Offer from Blitz Brigade' });
    expect(screen.getByText(/CPU managers only accept a trade that is fair in value for them and makes their own lineup better/)).toBeInTheDocument();
    expect(within(card).getByText('You give').parentElement).toHaveTextContent('Travis Etienne');
    expect(within(card).getByText('You give').parentElement).toHaveTextContent('31.5 value');
    expect(within(card).getByText('You get').parentElement).toHaveTextContent('35.0 value');
    expect(within(card).getByText(/Slight win for you/)).toBeInTheDocument();
    expect(within(card).getByRole('meter', { name: 'Trade balance' })).toHaveAttribute('aria-valuenow', '10');
    expect(within(card).getByText(/you \+3\.2, Blitz Brigade \+1\.1/)).toBeInTheDocument();

    const history = screen.getByRole('table', { name: 'Trades and offers so far' });
    expect(within(history).getByRole('row', { name: /3 Your Team Christian McCaffrey Gridiron Ghosts Nico Collins Declined/ })).toBeInTheDocument();
    await user.click(within(history).getByRole('button', { name: 'Nico Collins' }));
    expect(openPlayer).toHaveBeenCalledWith(COLLINS.id);
  });

  it('accepts an offer and says the trade was executed', async () => {
    const reply = { ...actionResult(), accepted: true, expired: false, message: null, verdict: null };
    const { api, user } = renderTrades(trades({ offers: [offer()] }), [{ method: 'POST', path: RESPOND_PATH, reply: json(reply) }]);
    await user.click(await screen.findByRole('button', { name: 'Accept offer from Blitz Brigade' }));

    expect(await screen.findByText('Trade executed with Blitz Brigade.')).toHaveAttribute('role', 'status');
    expect(api.callsTo(RESPOND_PATH, 'POST').map((call) => call.body)).toEqual([{ accept: true, drops: [] }]);
  });

  it('declines an offer', async () => {
    const reply = { ...actionResult(), accepted: false, expired: false, message: null, verdict: null };
    const { api, user } = renderTrades(trades({ offers: [offer()] }), [{ method: 'POST', path: RESPOND_PATH, reply: json(reply) }]);
    await user.click(await screen.findByRole('button', { name: 'Decline offer from Blitz Brigade' }));

    expect(await screen.findByText("You declined Blitz Brigade's offer.")).toHaveAttribute('role', 'status');
    expect(api.callsTo(RESPOND_PATH, 'POST')[0].body).toEqual({ accept: false, drops: [] });
  });

  it('shows why an offer expired', async () => {
    const message = 'That offer no longer stands. Blitz Brigade has already made a trade this week.';
    const reply = { ...actionResult(), accepted: false, expired: true, message, verdict: null };
    const { user } = renderTrades(trades({ offers: [offer()] }), [{ method: 'POST', path: RESPOND_PATH, reply: json(reply) }]);
    await user.click(await screen.findByRole('button', { name: 'Accept offer from Blitz Brigade' }));
    expect(await screen.findByText(message)).toHaveAttribute('role', 'status');
  });

  it('shows the server message next to the offer when the answer is refused', async () => {
    const detail = 'This trade doesn\'t need you to drop anyone.';
    const { user } = renderTrades(trades({ offers: [offer()] }), [{ method: 'POST', path: RESPOND_PATH, reply: json({ detail }, 422) }]);
    await user.click(await screen.findByRole('button', { name: 'Accept offer from Blitz Brigade' }));
    expect(await within(screen.getByRole('listitem', { name: 'Offer from Blitz Brigade' })).findByText(detail)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Accept offer from Blitz Brigade' })).toBeEnabled();
  });

  it('makes the user choose a drop when the offer overfills the roster, and sends it', async () => {
    const full: ReplaySummary = summary({ settings: { ...summary().settings, roster_size: 11 } });
    const note = 'This trade leaves you 1 over the roster limit of 11: choose exactly 1 to drop.';
    const twoForOne = offer({ give: [MOSTERT, COLLINS], verdict: null, note });
    const withDrop = verdict({ your_drops: [MCCAFFREY], get: [{ player: MOSTERT, value: 35 }, { player: COLLINS, value: 18 }] });
    const { api, user } = renderTrades(trades({ offers: [twoForOne] }), [
      { method: 'POST', path: EVALUATE_PATH, reply: json({ verdict: withDrop }) },
      { method: 'POST', path: RESPOND_PATH, reply: json({ ...actionResult(full), accepted: true, expired: false, message: null, verdict: null }) },
    ], { props: { game: full } });

    const card = await screen.findByRole('listitem', { name: 'Offer from Blitz Brigade' });
    const accept = within(card).getByRole('button', { name: 'Accept offer from Blitz Brigade' });
    expect(within(card).getByText(note)).toBeInTheDocument();
    expect(accept).toBeDisabled();
    expect(within(card).queryByRole('checkbox', { name: /Drop Travis Etienne/ })).not.toBeInTheDocument();      // he is in the deal

    await user.click(within(card).getByRole('checkbox', { name: /Drop Christian McCaffrey/ }));
    expect(within(card).getByRole('checkbox', { name: /Drop Patrick Mahomes/ })).toBeDisabled();                  // exactly one
    expect(await within(card).findByText('You would drop Christian McCaffrey.')).toBeInTheDocument();
    expect(api.callsTo(EVALUATE_PATH, 'POST')[0].body).toEqual({ to_team: 2, give: [ETIENNE.id], get: [MOSTERT.id, COLLINS.id], drops: [MCCAFFREY.id] });

    await user.click(accept);
    await waitFor(() => expect(api.callsTo(RESPOND_PATH, 'POST')[0]?.body).toEqual({ accept: true, drops: [MCCAFFREY.id] }));
  });
});

describe('TradesView builder', () => {
  it('evaluates as soon as both sides have a player and shows the verdict', async () => {
    const declined = verdict({
      accepted: false, fair_label: 'big_win', percent: 0.4, their_drops: [COLLINS],
      reasons: ['Value is too far apart: Gridiron Ghosts would give 35.0 points of value and get 31.5 back.'],
      suggestion: 'Add Isiah Pacheco to make this fair.',
    });
    const { api, user } = renderTrades(trades(), [{ method: 'POST', path: EVALUATE_PATH, reply: json({ verdict: declined }) }]);

    expect(await screen.findByText('Tick at least one player on each side to see the verdict.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Propose trade' })).toBeDisabled();
    await tickBothSides(user);

    expect(await screen.findByText(/You give 31\.5 value, get 35\.0\./)).toBeInTheDocument();
    expect(api.callsTo(EVALUATE_PATH, 'POST').map((call) => call.body)).toEqual([{ to_team: 1, give: [ETIENNE.id], get: [MOSTERT.id], drops: [] }]);
    expect(screen.getByRole('meter', { name: 'Trade balance' })).toHaveAttribute('aria-valuenow', '40');
    expect(screen.getByText(/Big win for you/)).toBeInTheDocument();
    expect(screen.getByText('Gridiron Ghosts would turn this down:')).toBeInTheDocument();
    expect(screen.getByText(/Value is too far apart/)).toBeInTheDocument();
    expect(screen.getByText('Add Isiah Pacheco to make this fair.')).toBeInTheDocument();
    expect(screen.getByText('Gridiron Ghosts would drop Nico Collins.')).toBeInTheDocument();
    expect(screen.getByText(/you \+3\.2, Gridiron Ghosts \+1\.1/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Propose trade' })).toBeEnabled();
  });

  it('proposes the trade, says it was executed and clears the builder', async () => {
    let finish: (() => void) | undefined;
    const executed = { ...actionResult(), verdict: verdict(), executed: true };
    const propose = () => new Promise<MockReply>((resolve) => { finish = () => resolve(json(executed)); });
    const { api, user } = renderTrades(trades(), [
      { method: 'POST', path: EVALUATE_PATH, reply: json({ verdict: verdict() }) },
      { method: 'POST', path: PROPOSE_PATH, reply: propose },
    ]);
    await tickBothSides(user);
    expect(await screen.findByText('Gridiron Ghosts would accept this trade.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Propose trade' }));

    const pending = await screen.findByRole('button', { name: 'Proposing…' });
    expect(pending).toBeDisabled();
    await user.click(pending);
    finish?.();

    expect(await screen.findByText('Trade executed with Gridiron Ghosts.')).toHaveAttribute('role', 'status');
    expect(api.callsTo(PROPOSE_PATH, 'POST').map((call) => call.body)).toEqual([{ to_team: 1, give: [ETIENNE.id], get: [MOSTERT.id], drops: [] }]);
    expect(screen.getByRole('checkbox', { name: 'Give Travis Etienne' })).not.toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Get Raheem Mostert' })).not.toBeChecked();
  });

  it('lists the reasons when the CPU turns the proposal down', async () => {
    const no = verdict({ accepted: false, reasons: ["It doesn't improve Gridiron Ghosts's lineup: they are already deep at RB."] });
    const { user } = renderTrades(trades(), [
      { method: 'POST', path: EVALUATE_PATH, reply: json({ verdict: no }) },
      { method: 'POST', path: PROPOSE_PATH, reply: json({ ...actionResult(), verdict: no, executed: false }) },
    ]);
    await tickBothSides(user);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Propose trade' })).toBeEnabled());
    await user.click(screen.getByRole('button', { name: 'Propose trade' }));

    const result = (await screen.findByText('Gridiron Ghosts turned it down.')).closest('[role="status"]');
    expect(result).toHaveTextContent('they are already deep at RB');
    expect(screen.getByRole('checkbox', { name: 'Give Travis Etienne' })).toBeChecked();
  });

  it('shows the server message inline when the proposal is refused', async () => {
    const detail = 'Gridiron Ghosts has already made a trade this week.';
    const { user } = renderTrades(trades(), [
      { method: 'POST', path: EVALUATE_PATH, reply: json({ verdict: verdict() }) },
      { method: 'POST', path: PROPOSE_PATH, reply: json({ detail }, 422) },
    ]);
    await tickBothSides(user);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Propose trade' })).toBeEnabled());
    await user.click(screen.getByRole('button', { name: 'Propose trade' }));
    expect(await screen.findByText(detail)).toBeInTheDocument();
  });

  it('shows why a trade cannot be evaluated', async () => {
    const detail = 'Not on Gridiron Ghosts\'s roster: Raheem Mostert.';
    const { user } = renderTrades(trades(), [{ method: 'POST', path: EVALUATE_PATH, reply: json({ detail }, 422) }]);
    await tickBothSides(user);
    expect(await screen.findByRole('alert')).toHaveTextContent(detail);
    expect(screen.getByRole('button', { name: 'Propose trade' })).toBeDisabled();
  });

  it('disables locked players with the reason, lets an out player be ticked, and caps each side at max_players', async () => {
    const { user } = renderTrades(trades({ max_players: 2 }));
    expect(await screen.findByRole('checkbox', { name: "Give Puka Nacua (can't be traded: traded in the last two weeks)" })).toBeDisabled();
    await user.click(screen.getByRole('checkbox', { name: 'Give Chris Olave' }));     // out this week, on the active roster
    expect(screen.getByRole('checkbox', { name: 'Give Chris Olave' })).toBeChecked();
    await user.click(screen.getByRole('checkbox', { name: 'Give Chris Olave' }));
    expect(await screen.findByRole('checkbox', { name: "Get Nico Collins (can't be traded: traded in the last two weeks)" })).toBeDisabled();

    await user.click(screen.getByRole('checkbox', { name: 'Give Travis Etienne' }));
    await user.click(screen.getByRole('checkbox', { name: 'Give Patrick Mahomes' }));
    expect(screen.getByRole('checkbox', { name: 'Give Travis Kelce' })).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: 'Give Travis Etienne' })).toBeEnabled();
  });

  it('starts from the partner and player in the link', async () => {
    const { api } = renderTrades(trades(), [], { hash: `#/replay?game=${GAME_ID}&view=trades&with=2&want=${MOSTERT.id}` });
    expect(await screen.findByRole('checkbox', { name: 'Get Raheem Mostert' })).toBeChecked();
    expect(screen.getByRole('combobox', { name: 'Trade with' })).toHaveValue('2');
    expect(screen.queryByRole('option', { name: /Your Team/ })).not.toBeInTheDocument();
    expect(api.callsTo(`${PATH}/team`).map((call) => call.url.searchParams.get('team_id'))).toEqual(['4', '2']);
  });

  it('loads the other roster when the partner changes', async () => {
    const { api, user } = renderTrades(trades());
    await user.selectOptions(await screen.findByRole('combobox', { name: 'Trade with' }), 'Hail Marys');
    expect(await screen.findByRole('table', { name: "Hail Marys's roster" })).toBeInTheDocument();
    expect(api.callsTo(`${PATH}/team`).at(-1)?.url.searchParams.get('team_id')).toBe('5');
  });
});

describe('TradesView rules and errors', () => {
  it('closes the builder after the deadline and only lets an offer be declined', async () => {
    renderTrades(trades({ open: false, offers: [offer()] }), [], { props: { game: summary({ week: 12 }) } });
    expect(await screen.findByText('The trade deadline has passed: no trades after week 11.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Propose trade' })).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Accept offer from Blitz Brigade' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Decline offer from Blitz Brigade' })).toBeEnabled();
  });

  it('mentions the extra value CPUs want on Hard, and the empty lists', async () => {
    renderTrades(trades(), [], { props: { game: summary({ settings: { ...summary().settings, difficulty: 'hard' } }) } });
    expect(await screen.findByText('No offers are waiting for your answer.')).toBeInTheDocument();
    expect(screen.getByText('No trades or offers yet.')).toBeInTheDocument();
    expect(screen.getByText(/on Hard they want about 8% more than they give/)).toBeInTheDocument();
  });

  it('says so when the trades cannot be loaded', async () => {
    mockFetch([META_ROUTE, { path: TRADES_PATH, reply: json({ detail: 'Season data is not prepared.' }, 503) }, { path: `${PATH}/team`, reply: teamReply }]);
    renderWithProviders(<TradesView {...viewProps()} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Season data is not prepared.');
  });
});

describe('TradesView injuries and IR', () => {
  it('states the dealing rules, including IR and the CPUs\' out discount', async () => {
    renderTrades(trades({ max_players: 2 }));
    expect(await screen.findByText("Up to 2 players a side. A player on IR or traded in the last two weeks can't be dealt. "
      + 'CPUs count a player who is out this week at 80% of his value.')).toBeInTheDocument();
  });

  it('shows the IR banner while blocked, and leaves the offer answerable', async () => {
    renderTrades(trades({ offers: [offer()] }), [], { props: { game: summary({ ir_blocked: [COLLINS] }) } });
    const card = await screen.findByRole('listitem', { name: 'Offer from Blitz Brigade' });
    expect(screen.getByText('Nico Collins is on IR but no longer has an O, IR or PUP tag. Activate or drop him before you add, claim or trade for players.'))
      .toHaveAttribute('role', 'status');
    await waitFor(() => expect(within(card).getByRole('button', { name: /Accept/ })).toBeEnabled());
  });

  it('lists a partner\'s IR player from a link as untradable instead of dropping him', async () => {
    const hurt = tagged(ETIENNE, 'IR', null, 'Knee');
    const irRow = { ...rosterRow(hurt, 'BN', { out: true, projection: 0 }), slot: 'IR' as const, starting: false as const };
    const reply = (request: MockRequest) => (Number(request.url.searchParams.get('team_id')) === 2
      ? json(team({ team: teamRef(2), results: [rosterRow(MOSTERT, 'RB', { value: 35 })], ir: [irRow] })) : teamReply(request));
    const { api } = renderTrades(trades(), [{ path: `${PATH}/team`, reply }], { hash: `#/replay?game=${GAME_ID}&view=trades&with=2&want=${hurt.id}` });

    const box = await screen.findByRole('checkbox', { name: "Get Travis Etienne (can't be traded: on IR)" });
    expect(box).toBeDisabled();
    expect(box).not.toBeChecked();
    expect(within(box.closest('tr')!).getByText('On IR')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Propose trade' })).toBeDisabled();
    expect(api.callsTo(EVALUATE_PATH, 'POST')).toHaveLength(0);
  });

  it('shows the tag on an offer\'s player', async () => {
    const hurt = tagged(MOSTERT, 'Q', 'LP', 'Hamstring');
    renderTrades(trades({ offers: [offer({ give: [hurt], verdict: verdict({ get: [{ player: hurt, value: 35 }] }) })] }));
    const card = await screen.findByRole('listitem', { name: 'Offer from Blitz Brigade' });
    expect(within(card).getByTitle('Questionable: Hamstring (limited practice)')).toHaveTextContent('Q');
  });
});
