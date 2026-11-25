import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ReplayClaim, ReplayClaims, ReplayPlayerRef, ReplaySummary, ReplayWireRow } from '../../api/types';
import { json, mockFetch, type MockReply, type MockRoute } from '../../test/mockFetch';
import { AppProviders } from '../../App';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import {
  actionResult, COLLINS, COOK, GAME_ID, HILL, MAHOMES, MOSTERT, nextAction, OLAVE, OPPONENT_TEAM_ID, playerRow, rosterRow,
  statLine, summary, team, teamRef, TEAMS, usage, USER_TEAM_ID, useTestHub, viewProps, wireRow,
} from './fixtures';
import { boxLine, defense } from './fixturesPlayers';
import { PlayersView } from './PlayersView';

const GAME_PATH = `/api/replay/games/${GAME_ID}`;
const PLAYERS = `${GAME_PATH}/players`;
const CLAIMS = `${GAME_PATH}/claims`;
const TEAM = `${GAME_PATH}/team`;
const ADD_DROP = `${GAME_PATH}/add-drop`;

const WAIVERS = summary({ phase: 'waivers', next_action: nextAction('wednesday') });
/** The fixture roster has 11 players, so this league's rosters are full. */
const FULL = summary({ settings: { ...summary().settings, roster_size: 11 } });
const ROSTERED = wireRow(HILL, { status: 'rostered', owner: teamRef(OPPONENT_TEAM_ID) });
const MINE = wireRow(MAHOMES, { status: 'rostered', owner: teamRef(USER_TEAM_ID) });

function wire(results: ReplayWireRow[], total = results.length) {
  return { total, limit: 25, offset: 0, week: 5, results };
}

function claim(id: number, order: number, add: ReplayPlayerRef, drop: ReplayPlayerRef | null = null): ReplayClaim {
  return { id, order, add: playerRow(add), drop: drop && playerRow(drop), bid: 0 };
}

function claims(results: ReplayClaim[] = []): ReplayClaims {
  return { phase: 'waivers', can_claim: true, priority: 4, waiver_order: TEAMS, results, faab: null };
}

function Hub({ game }: { game: ReplaySummary }) {
  return <PlayersView {...viewProps({ game, ...useTestHub() })} />;
}

function renderPlayers(game: ReplaySummary, routes: MockRoute[] = []) {
  const api = mockFetch([META_ROUTE, ...routes,
    { path: PLAYERS, reply: json(wire([wireRow(MOSTERT), wireRow(COLLINS, { status: 'waivers' }), ROSTERED, MINE])) },
    { path: TEAM, reply: json(team()) },
    { path: CLAIMS, reply: json(claims()) }]);
  renderWithProviders(<Hub game={game} />);
  return { api, user: userEvent.setup(), lastQuery: () => api.callsTo(PLAYERS).at(-1)!.url.searchParams };
}

function post(path: string, reply: MockReply | (() => Promise<MockReply>) = json(actionResult())): MockRoute {
  return { method: 'POST', path, reply };
}

/** A move button once the roster is in (moves wait for it). */
async function ready(name: string) {
  const button = await screen.findByRole('button', { name });
  await waitFor(() => expect(button).toBeEnabled());
  return button;
}

async function row(name: string) {
  return within(await screen.findByRole('row', { name: new RegExp(name) }));
}

describe('PlayersView table', () => {
  it('lists the wire with each player\'s status and what can be done while the week is open', async () => {
    const { api, lastQuery } = renderPlayers(summary());
    const mostert = await row('Raheem Mostert');
    expect(screen.getByRole('table', { name: 'Players, week 5' })).toBeInTheDocument();
    expect(mostert.getByTitle('Free agent')).toHaveTextContent('FA');
    expect(mostert.getByRole('button', { name: 'Add Raheem Mostert' })).toBe(await ready('Add Raheem Mostert'));
    expect(mostert.getByText('60.40')).toBeInTheDocument();

    const collins = await row('Nico Collins');
    expect(collins.getByTitle('On waivers')).toHaveTextContent('W');
    expect(collins.getByText('Next week')).toBeInTheDocument();
    expect(collins.queryByRole('button', { name: /Add|Claim/ })).not.toBeInTheDocument();

    const hill = await row('Tyreek Hill');
    expect(hill.getByText('Sunday Scaries')).toBeInTheDocument();
    expect(hill.getByRole('link', { name: 'Trade for Tyreek Hill' })).toHaveAttribute('href', '#/play?game=7&view=trades&with=9&want=104');
    expect((await row('Patrick Mahomes')).getByText('Your player')).toBeInTheDocument();

    expect(Object.fromEntries(lastQuery())).toEqual({ status: 'available', sort: 'value', dir: 'desc', stats: 'season', limit: '25', offset: '0' });
    expect(screen.getByText(/Week 5’s waivers have run, so every player still unrostered is a free agent/)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Waiver claims' })).not.toBeInTheDocument();
    expect(api.callsTo(CLAIMS)).toHaveLength(0);
  });

  it('never shows points that were not scored', async () => {
    renderPlayers(summary(), [{ path: PLAYERS, reply: json(wire([wireRow(MOSTERT, { games: 0, ppg: 0, line: statLine({ games: 0, points: 0 }) })])) }]);
    const cells = (await row('Raheem Mostert')).getAllByRole('cell').map((cell) => cell.textContent);
    // Opp, Pts, PPG, GP, ROS, seven box columns (no box yet), Value.
    expect(cells.slice(2, 15)).toEqual(['DEN', '0.00', '—', '0', '13.80', '—', '—', '—', '—', '—', '—', '—', '32']);
  });

  it('shows the box columns that fit the position, and the matchup in words as well as colour', async () => {
    const { user } = renderPlayers(summary(), [{ path: PLAYERS, reply: json(wire([
      wireRow(MOSTERT, { line: statLine({ box: boxLine() }), defense: defense('easy', 3) }), wireRow(COLLINS, { defense: defense('tough', 29) }),
    ])) }]);
    const mostert = await row('Raheem Mostert');
    expect(mostert.getAllByRole('cell').slice(7, 14).map((cell) => cell.textContent)).toEqual(['15', '72', '1', '4', '3', '21', '0']);
    expect(mostert.getByText('Easy · 3rd')).toHaveClass('game-chip--easy');
    expect((await row('Nico Collins')).getByText('Tough · 29th')).toHaveClass('game-chip--tough');
    expect(screen.getByRole('columnheader', { name: /RuYd/ })).toHaveAttribute('title', 'Rushing yards, this season');

    await user.click(within(screen.getByRole('group', { name: 'Filter by position' })).getByRole('button', { name: 'QB' }));
    expect(await screen.findByRole('columnheader', { name: /PaYd/ })).toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: /Car/ })).not.toBeInTheDocument();
  });

  it('switches to the Advanced columns, sorts by one on the server, and falls back to Value on Box score', async () => {
    const adv = { snap_pct: 71.2, target_share: 12.5, targets_pg: 3.1, carries_pg: 14, touches_pg: 16.5, ypc: 4.8, ypr: 7.9, ypt: 6.2,
      catch_rate: 78.6, total_td: 9, td_rate: 4.1, pass_ypg: null, td_int: null };
    const { user, lastQuery } = renderPlayers(summary(), [{ path: PLAYERS, reply: json(wire([wireRow(MOSTERT, { line: statLine({ adv }) })])) }]);
    await row('Raheem Mostert');
    const views = screen.getByRole('group', { name: 'Stat columns' });
    expect(within(views).getByRole('button', { name: 'Box score' })).toHaveAttribute('aria-pressed', 'true');

    await user.click(within(views).getByRole('button', { name: 'Advanced' }));
    const mostert = await row('Raheem Mostert');
    expect(screen.getByRole('columnheader', { name: /Snap%/ })).toHaveAttribute('title', 'Share of his team’s offensive snaps, this season');
    expect(mostert.getByText('71.2%')).toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: /RuYd/ })).not.toBeInTheDocument();

    await user.click(within(screen.getByRole('columnheader', { name: /YPC/ })).getByRole('button'));
    await waitFor(() => expect(lastQuery().get('sort')).toBe('adv_ypc'));
    await user.click(within(views).getByRole('button', { name: 'Box score' }));
    await waitFor(() => expect(lastQuery().get('sort')).toBe('value'));
  });

  it('pins Player on the left and Value with the row action on the right', async () => {
    renderPlayers(summary());
    const mostert = await row('Raheem Mostert');
    const cells = mostert.getAllByRole('cell');
    expect(cells[0]).toHaveClass('replay-players__pin-start');
    expect(cells.at(-2)).toHaveClass('replay-players__pin-value');
    expect(cells.at(-1)).toHaveClass('replay-players__pin-action');
  });

  it('keeps the old headers over the old rows until the new tab rows arrive', async () => {
    let finish: (reply: MockReply) => void = () => {};
    const { user } = renderPlayers(summary(), [{
      path: PLAYERS,
      reply: ({ url }) => (url.searchParams.get('position') === 'QB'
        ? new Promise<MockReply>((resolve) => { finish = resolve; })
        : json(wire([wireRow(MOSTERT)]))),
    }]);
    await row('Raheem Mostert');
    await user.click(within(screen.getByRole('group', { name: 'Filter by position' })).getByRole('button', { name: 'QB' }));
    expect(screen.getByRole('columnheader', { name: /RuYd/ })).toBeInTheDocument();      // still the ALL page's rows
    expect(screen.queryByRole('columnheader', { name: /PaYd/ })).not.toBeInTheDocument();
    finish(json(wire([wireRow(MAHOMES)])));
    expect(await screen.findByRole('columnheader', { name: /PaYd/ })).toBeInTheDocument();
    expect(await row('Patrick Mahomes')).toBeTruthy();
  });

  it('drops a sort whose column went away, so going back does not re-sort', async () => {
    const { user, lastQuery } = renderPlayers(summary());
    const tabs = within(await screen.findByRole('group', { name: 'Filter by position' }));
    await user.click(tabs.getByRole('button', { name: 'WR' }));
    await user.click(await screen.findByRole('button', { name: /^Tgt/ }));
    await waitFor(() => expect(lastQuery().get('sort')).toBe('targets'));
    await user.click(tabs.getByRole('button', { name: 'QB' }));
    await waitFor(() => expect(lastQuery().get('position')).toBe('QB'));
    expect(lastQuery().get('sort')).toBe('value');
    await user.click(tabs.getByRole('button', { name: 'WR' }));
    await waitFor(() => expect(lastQuery().get('position')).toBe('WR'));
    expect(lastQuery().get('sort')).toBe('value');
  });

  it('shows injury news even for a player without an injury tag', async () => {
    renderPlayers(summary(), [{ path: PLAYERS, reply: json(wire([wireRow(MOSTERT, { news: { back: 7, text: 'Expected back in week 7.' } })])) }]);
    expect((await row('Raheem Mostert')).getByText('Expected back in week 7.')).toBeInTheDocument();
  });

  it('sorts on the server when a header is clicked, and a second click flips it', async () => {
    const { user, lastQuery } = renderPlayers(summary(), [{ path: PLAYERS, reply: json(wire([wireRow(MOSTERT)], 60)) }]);
    await user.click(await screen.findByRole('button', { name: 'Next' }));
    await waitFor(() => expect(lastQuery().get('offset')).toBe('25'));
    expect(screen.getByRole('columnheader', { name: /Value/ })).toHaveAttribute('aria-sort', 'descending');

    await user.click(screen.getByRole('button', { name: /^RuYd/ }));
    await waitFor(() => expect(lastQuery().get('sort')).toBe('rush_yds'));
    expect(lastQuery().get('dir')).toBe('desc');
    expect(lastQuery().get('offset')).toBe('0');                       // a new sort starts on page 1
    expect(screen.getByRole('columnheader', { name: /RuYd/ })).toHaveAttribute('aria-sort', 'descending');
    expect(screen.getByRole('columnheader', { name: /Value/ })).toHaveAttribute('aria-sort', 'none');

    await user.click(screen.getByRole('button', { name: /^RuYd/ }));
    await waitFor(() => expect(lastQuery().get('dir')).toBe('asc'));
    expect(screen.getByRole('columnheader', { name: /RuYd/ })).toHaveAttribute('aria-sort', 'ascending');

    await user.click(screen.getByRole('button', { name: /^Player/ }));
    await waitFor(() => expect(lastQuery().get('sort')).toBe('name'));
    expect(lastQuery().get('dir')).toBe('asc');
    expect(screen.queryByLabelText('Sort by')).not.toBeInTheDocument();
  });

  it('switches the stat columns and the request with the Stats picker', async () => {
    const { user, lastQuery } = renderPlayers(summary(), [{
      path: PLAYERS,
      reply: ({ url }) => json(wire([wireRow(MOSTERT, {
        usage: usage(),
        line: url.searchParams.get('stats') === 'week'
          ? statLine({ span: 'week', week: 5, points: null, projection: 14.2, box: null })
          : statLine({ span: 'last4', points: 15.1, box: boxLine({ rush_yds: 64.25 }) }),
      })])),
    }]);
    const picker = await screen.findByLabelText('Stats');
    expect(within(picker).getAllByRole('option').map((option) => option.textContent)).toEqual(
      ['Season totals', 'Last 4 games (avg)', 'Week 1', 'Week 2', 'Week 3', 'Week 4', 'Week 5 (proj)']);

    await user.click(await screen.findByRole('button', { name: /^Pts/ }));
    await waitFor(() => expect(lastQuery().get('sort')).toBe('line_points'));
    await user.selectOptions(picker, 'Last 4 games (avg)');
    await waitFor(() => expect(lastQuery().get('stats')).toBe('last4'));
    expect(lastQuery().get('sort')).toBe('line_points');               // Pts/G is the same server column
    expect(await screen.findByRole('columnheader', { name: /Pts\/G/ })).toHaveAttribute('aria-sort', 'descending');
    const last4 = await row('Raheem Mostert');
    expect(last4.getAllByRole('cell').slice(3, 5).map((cell) => cell.textContent)).toEqual(['15.10', '72%']);
    expect(last4.getByText('64.3')).toBeInTheDocument();

    await user.selectOptions(picker, 'Week 5 (proj)');
    await waitFor(() => expect(lastQuery().get('stat_week')).toBe('5'));
    expect(lastQuery().get('stats')).toBe('week');
    await screen.findByRole('columnheader', { name: /Proj/ });
    // Week 5 hasn't been played: its projection, and a dash for points.
    await waitFor(async () => expect((await row('Raheem Mostert')).getAllByRole('cell').slice(3, 5).map((cell) => cell.textContent))
      .toEqual(['14.20', '—']));
  });

  it('sends the filters, the debounced search and the page to the API', async () => {
    const { user, lastQuery } = renderPlayers(summary(), [{ path: PLAYERS, reply: json(wire([wireRow(MOSTERT)], 60)) }]);
    await user.click(await screen.findByRole('button', { name: 'Next' }));
    await waitFor(() => expect(lastQuery().get('offset')).toBe('25'));
    expect(screen.getByText('1–25 of 60')).toBeInTheDocument();

    await user.click(within(screen.getByRole('group', { name: 'Filter by position' })).getByRole('button', { name: 'FLEX' }));
    await waitFor(() => expect(lastQuery().get('position')).toBe('FLEX'));
    expect(lastQuery().get('offset')).toBe('0');
    expect(screen.getByRole('button', { name: 'FLEX' })).toHaveAttribute('aria-pressed', 'true');

    await user.selectOptions(screen.getByLabelText('Show'), 'Rostered');
    await user.type(screen.getByLabelText('Search'), ' mos');
    await waitFor(() => expect(lastQuery().get('search')).toBe('mos'));
    expect(Object.fromEntries(lastQuery())).toEqual({ position: 'FLEX', search: 'mos', status: 'rostered', sort: 'value', dir: 'desc', stats: 'season', limit: '25', offset: '0' });
  });

  it('caps the search at the backend\'s 60 characters', async () => {
    const { user, lastQuery } = renderPlayers(summary());
    await user.type(screen.getByLabelText('Search'), 'x'.repeat(70));
    await waitFor(() => expect(lastQuery().get('search')).toHaveLength(60));
  });

  it('steps back from a page that came back empty', async () => {
    const { user, api } = renderPlayers(summary(), [{
      path: PLAYERS,
      reply: ({ url }) => (url.searchParams.get('offset') === '25'
        ? json({ total: 25, limit: 25, offset: 25, week: 5, results: [] })
        : json(wire([wireRow(MOSTERT)], 26))),
    }]);
    await user.click(await screen.findByRole('button', { name: 'Next' }));
    await waitFor(() => expect(api.callsTo(PLAYERS).map((call) => call.url.searchParams.get('offset'))).toEqual(['0', '25', '0']));
    expect(await row('Raheem Mostert')).toBeTruthy();
    expect(screen.queryByText('No players match')).not.toBeInTheDocument();
  });

  it('shows the error state', async () => {
    renderPlayers(summary(), [{ path: PLAYERS, reply: json({ detail: 'Season 2023 is not prepared.' }, 503) }]);
    expect(await screen.findByRole('alert')).toHaveTextContent('Season 2023 is not prepared.');
  });

  it('says when nobody matches', async () => {
    renderPlayers(summary(), [{ path: PLAYERS, reply: json(wire([])) }]);
    expect(await screen.findByText('No players match')).toBeInTheDocument();
  });

  it('offers no moves once the season is over', async () => {
    const { api } = renderPlayers(summary({ phase: 'complete', next_action: nextAction('done') }));
    await row('Raheem Mostert');
    expect(screen.queryByRole('button', { name: /^(Add|Claim) / })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Trade/ })).not.toBeInTheDocument();
    expect(screen.getByText('The season is over, so rosters are final.')).toBeInTheDocument();
    expect(api.callsTo(TEAM)).toHaveLength(0);
  });

  it('offers no trade link once trades are closed', async () => {
    renderPlayers(summary({ week: 12, trades_open: false }));
    expect((await row('Tyreek Hill')).getByText('Sunday Scaries')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Trade/ })).not.toBeInTheDocument();
    expect(await ready('Add Raheem Mostert')).toBeInTheDocument();
  });

  it('explains a league without waivers', async () => {
    renderPlayers(summary({ settings: { ...summary().settings, waiver_mode: 'none' } }));
    expect(await screen.findByText(/This league has no waivers: every unrostered player is a free agent/)).toBeInTheDocument();
  });
});

describe('PlayersView adding a free agent', () => {
  it('adds straight away when there is room, shows it pending and reloads the wire', async () => {
    let finish: (reply: MockReply) => void = () => undefined;
    const { api, user } = renderPlayers(summary(), [post(ADD_DROP, () => new Promise<MockReply>((resolve) => { finish = resolve; }))]);
    await user.click(await ready('Add Raheem Mostert'));

    expect(await screen.findByRole('button', { name: 'Adding… Raheem Mostert' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Adding… Raheem Mostert' }));
    finish(json(actionResult()));
    // The notice takes focus in an effect, a moment after its text is on the page.
    await waitFor(() => expect(screen.getByText('Added Raheem Mostert.')).toHaveFocus());
    expect(api.callsTo(ADD_DROP, 'POST').map((call) => call.body)).toEqual([{ add: MOSTERT.id, week: 5 }]);
    await waitFor(() => expect(api.callsTo(PLAYERS)).toHaveLength(2));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('shows the server\'s reason in a full-width row under the player when the add is refused', async () => {
    const { user } = renderPlayers(summary(), [post(ADD_DROP, json({ detail: 'Raheem Mostert was just dropped and is on waivers until next week.' }, 422))]);
    await user.click(await ready('Add Raheem Mostert'));
    const reason = (await screen.findByText('Raheem Mostert was just dropped and is on waivers until next week.')).closest('tr')!;
    expect(within(reason.previousElementSibling as HTMLElement).getByRole('button', { name: 'Add Raheem Mostert' })).toBeEnabled();
    expect(reason.querySelector('td')).toHaveAttribute('colspan', String(reason.previousElementSibling!.children.length));
  });

  it('asks who to drop when the roster is full, least valuable first', async () => {
    const roster = team({ results: [...team().results.slice(0, 9), rosterRow(COOK, 'BN', { value: 9 }), rosterRow(OLAVE, 'BN', { value: 5, out: true })] });
    const { api, user } = renderPlayers(FULL, [{ path: TEAM, reply: json(roster) }, post(ADD_DROP)]);
    const opener = await ready('Add Raheem Mostert');
    await user.click(opener);

    const dialog = await screen.findByRole('dialog', { name: 'Add Raheem Mostert' });
    expect(within(dialog).getByRole('button', { name: 'Close' })).toHaveFocus();
    const choices = within(await within(dialog).findByRole('group', { name: 'Drop who?' })).getAllByRole('radio');
    expect(choices).toHaveLength(11);
    expect(choices[0]).toHaveAccessibleName(/Chris Olave.*Value 5(?![.\d])/);
    expect(choices[1]).toHaveAccessibleName(/James Cook/);
    expect(within(dialog).getByText(/Your roster is full \(11 players\)/)).toBeInTheDocument();
    expect(within(dialog).queryByRole('radio', { name: /Don't drop anyone/ })).not.toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Add and drop' })).toBeDisabled();

    await user.click(choices[0]);
    await user.click(within(dialog).getByRole('button', { name: 'Add and drop' }));
    expect(await screen.findByText('Added Raheem Mostert, dropping Chris Olave.')).toBeInTheDocument();
    expect(api.callsTo(ADD_DROP, 'POST')[0].body).toEqual({ add: MOSTERT.id, drop: OLAVE.id, week: 5 });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('keeps the dialog open with the server\'s reason on a refusal, and Escape gives focus back', async () => {
    const { user } = renderPlayers(FULL, [post(ADD_DROP, json({ detail: 'Your Team can\'t drop James Cook: he isn\'t on that roster.' }, 422))]);
    const opener = await ready('Add Raheem Mostert');
    await user.click(opener);
    const dialog = await screen.findByRole('dialog', { name: 'Add Raheem Mostert' });
    await user.click(await within(dialog).findByRole('radio', { name: /James Cook/ }));
    await user.click(within(dialog).getByRole('button', { name: 'Add and drop' }));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Your Team can\'t drop James Cook: he isn\'t on that roster.');

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it('shows the roster\'s error in the dialog when it could not be loaded', async () => {
    const { user } = renderPlayers(summary(), [{ path: TEAM, reply: json({ detail: 'boom' }, 500) }]);
    await user.click(await ready('Add Raheem Mostert'));
    const dialog = await screen.findByRole('dialog', { name: 'Add Raheem Mostert' });
    expect(await within(dialog).findByText('Couldn\'t load your roster')).toBeInTheDocument();
  });
});

describe('PlayersView waivers', () => {
  const PENDING = summary({ ...WAIVERS, pending: { claims: 2, offers: 0 } });
  const TWO_CLAIMS = { path: CLAIMS, reply: json(claims([claim(31, 1, MOSTERT, OLAVE), claim(32, 2, COLLINS)])) };

  it('shows the waiver order and puts in a claim, with the drop optional while there is room', async () => {
    const { api, user } = renderPlayers(WAIVERS, [post(CLAIMS),
      { path: PLAYERS, reply: json(wire([wireRow(MOSTERT, { status: 'waivers' }), ROSTERED])) }]);
    const panel = within(await screen.findByRole('region', { name: 'Waiver claims' }));
    expect(await panel.findByText('You are 4th of 12')).toBeInTheDocument();
    expect(panel.getByText(/claims are awarded in this order: a team that wins a player moves to the back of the line/)).toBeInTheDocument();
    expect(within(panel.getByRole('list', { name: 'Waiver order' })).getAllByRole('listitem')[3]).toHaveTextContent('Your Team (you)');
    expect(panel.getByText(/You have no claims in/)).toBeInTheDocument();
    expect(screen.getByText(/Every unrostered player is on waivers until you press “Sim to Wednesday”/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Add / })).not.toBeInTheDocument();

    await user.click(await ready('Claim Raheem Mostert'));
    const dialog = await screen.findByRole('dialog', { name: 'Claim Raheem Mostert' });
    expect(await within(dialog).findByRole('radio', { name: 'Don\'t drop anyone' })).toBeChecked();
    await user.click(within(dialog).getByRole('button', { name: 'Submit claim' }));
    expect(await screen.findByText('Claim for Raheem Mostert is in. It is settled on “Sim to Wednesday”.')).toBeInTheDocument();
    expect(api.callsTo(CLAIMS, 'POST')[0].body).toEqual({ add: MOSTERT.id, week: WAIVERS.week });
    await waitFor(() => expect(api.callsTo(CLAIMS)).toHaveLength(2));
  });

  it('drops the claim confirmation once the waivers have run', async () => {
    mockFetch([META_ROUTE, post(CLAIMS), { path: PLAYERS, reply: json(wire([wireRow(MOSTERT, { status: 'waivers' })])) },
      { path: TEAM, reply: json(team()) }, { path: CLAIMS, reply: json(claims()) }]);
    const user = userEvent.setup();
    const { rerender } = renderWithProviders(<Hub game={WAIVERS} />);
    await user.click(await ready('Claim Raheem Mostert'));
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Submit claim' }));
    const notice = 'Claim for Raheem Mostert is in. It is settled on “Sim to Wednesday”.';
    expect(await screen.findByText(notice)).toBeInTheDocument();

    rerender(<AppProviders><Hub game={summary()} /></AppProviders>);
    expect(screen.queryByText(notice)).not.toBeInTheDocument();
  });

  it('claims with a drop, and marks players already claimed', async () => {
    const { api, user } = renderPlayers(PENDING, [post(CLAIMS), { path: CLAIMS, reply: json(claims([claim(32, 1, COLLINS)])) },
      { path: PLAYERS, reply: json(wire([wireRow(MOSTERT, { status: 'waivers' }), wireRow(COLLINS, { status: 'waivers' })])) }]);
    expect(await (await row('Nico Collins')).findByText('Claim in')).toBeInTheDocument();
    await user.click(await ready('Claim Raheem Mostert'));
    const dialog = await screen.findByRole('dialog', { name: 'Claim Raheem Mostert' });
    await user.click(await within(dialog).findByRole('radio', { name: /Chris Olave/ }));
    await user.click(within(dialog).getByRole('button', { name: 'Submit claim' }));
    await waitFor(() => expect(api.callsTo(CLAIMS, 'POST')[0]?.body).toEqual({ add: MOSTERT.id, drop: OLAVE.id, week: 5 }));
  });

  it('reorders the pending claims', async () => {
    const { api, user } = renderPlayers(PENDING, [TWO_CLAIMS, post(`${CLAIMS}/reorder`)]);
    const list = within(await screen.findByRole('list', { name: 'Your pending claims' }));
    await ready('Cancel claim for Raheem Mostert');
    const items = list.getAllByRole('listitem');
    expect(items[0]).toHaveTextContent('Raheem Mostert');
    expect(items[0]).toHaveTextContent('Drop Chris Olave');
    expect(items[1]).toHaveTextContent('No drop');
    expect(list.getByRole('button', { name: 'Up, claim for Raheem Mostert' })).toBeDisabled();
    expect(list.getByRole('button', { name: 'Down, claim for Nico Collins' })).toBeDisabled();

    await user.click(list.getByRole('button', { name: 'Down, claim for Raheem Mostert' }));
    expect(await screen.findByText('Claim order updated.')).toBeInTheDocument();
    expect(api.callsTo(`${CLAIMS}/reorder`, 'POST')[0].body).toEqual({ ids: [32, 31] });
  });

  it('cancels a claim, and shows the reason when that fails', async () => {
    const { api, user } = renderPlayers(PENDING, [TWO_CLAIMS, post(`${CLAIMS}/31/cancel`),
      post(`${CLAIMS}/32/cancel`, json({ detail: 'There is no pending claim 32 to cancel.' }, 422))]);
    await user.click(await ready('Cancel claim for Raheem Mostert'));
    await waitFor(() => expect(screen.getByText('Claim for Raheem Mostert cancelled.')).toHaveFocus());
    expect(api.callsTo(`${CLAIMS}/31/cancel`, 'POST')).toHaveLength(1);

    await user.click(screen.getByRole('button', { name: 'Cancel claim for Nico Collins' }));
    expect(await screen.findByText('There is no pending claim 32 to cancel.')).toBeInTheDocument();
    expect(screen.queryByText('Claim for Raheem Mostert cancelled.')).not.toBeInTheDocument();
  });

  it('shows an error when the claims cannot be loaded', async () => {
    renderPlayers(WAIVERS, [{ path: CLAIMS, reply: json({ detail: 'boom' }, 500) }]);
    expect(await within(await screen.findByRole('region', { name: 'Waiver claims' })).findByRole('alert')).toHaveTextContent('Couldn\'t load your waiver claims');
  });
});

describe('PlayersView while an IR player has lost his tag', () => {
  const BANNER = 'Raheem Mostert is on IR but no longer has an O, IR or PUP tag. Activate or drop him before you add, claim or trade for players.';

  it('shows the banner and disables Add', async () => {
    renderPlayers(summary({ ir_blocked: [MOSTERT] }), [{ path: PLAYERS, reply: json(wire([wireRow(COLLINS)])) }]);
    const add = (await row('Nico Collins')).getByRole('button', { name: 'Add Nico Collins' });
    expect(screen.getByText(BANNER)).toHaveAttribute('role', 'status');
    await waitFor(() => expect(screen.queryAllByRole('table')).toHaveLength(1));
    expect(add).toBeDisabled();
    expect(add).toHaveAttribute('title', BANNER);
  });

  it('disables Claim during waivers', async () => {
    renderPlayers({ ...WAIVERS, ir_blocked: [MOSTERT] }, [{ path: PLAYERS, reply: json(wire([wireRow(COLLINS, { status: 'waivers' })])) }]);
    const claimButton = (await row('Nico Collins')).getByRole('button', { name: 'Claim Nico Collins' });
    expect(claimButton).toBeDisabled();
    expect(claimButton).toHaveAttribute('title', BANNER);
  });
});

describe('PlayersView pinned-column shadows', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('watches the table too, so a wider table (another stats view) shows the right-edge shadow', async () => {
    const observers: { callback: () => void; targets: Element[] }[] = [];
    vi.stubGlobal('ResizeObserver', class {
      targets: Element[] = [];
      constructor(callback: () => void) { observers.push({ callback, targets: this.targets }); }
      observe(target: Element) { this.targets.push(target); }
      disconnect() { this.targets.length = 0; }
    });
    renderPlayers(summary());
    const table = await screen.findByRole('table', { name: 'Players, week 5' });
    const box = table.parentElement!;
    const watcher = observers.find((o) => o.targets.includes(table));
    expect(watcher?.targets).toContain(box);
    expect(box).not.toHaveAttribute('data-overflow-end');

    Object.defineProperty(box, 'clientWidth', { configurable: true, value: 320 });
    Object.defineProperty(box, 'scrollWidth', { configurable: true, value: 900 });   // the table grew; the box did not
    act(() => watcher!.callback());

    expect(box).toHaveAttribute('data-overflow-end', 'true');
  });
});
