import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { ReplayIrRow, ReplayTeam } from '../../api/types';
import { json, mockFetch, type MockReply, type MockRoute } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import {
  actionResult, COOK, ETIENNE, GAME_ID, KELCE, MAHOMES, MOSTERT, nextAction, OLAVE, OPPONENT_TEAM_ID, PACHECO, playerRow, rosterRow, summary,
  tagged, team, teamRef, viewProps,
} from './fixtures';
import { defenseRating, playedTeam } from './fixturesTeam';
import type { ReplayViewProps } from './shared';
import { TeamView } from './TeamView';

const TEAM_PATH = `/api/replay/games/${GAME_ID}/team`;
const LINEUP_PATH = `/api/replay/games/${GAME_ID}/lineup`;
const DROP_PATH = `/api/replay/games/${GAME_ID}/drop`;
const lineupReply = (roster: ReplayTeam = team()) => json({ ...actionResult(), team: roster });

function renderTeam(roster: ReplayTeam = team(), props: Partial<ReplayViewProps> = {}, routes: MockRoute[] = []) {
  const api = mockFetch([META_ROUTE, ...routes, { path: TEAM_PATH, reply: json(roster) }]);
  renderWithProviders(<TeamView {...viewProps(props)} />);
  return { api, user: userEvent.setup() };
}

const table = () => screen.findByRole('table', { name: 'Your Team roster, week 5' });
const rowOf = (name: string) => screen.getByRole('row', { name: new RegExp(name) });

describe('TeamView roster', () => {
  it('shows the user\'s team this week: starters, total, bench, projections and no results', async () => {
    const openPlayer = vi.fn();
    const { api, user } = renderTeam(team(), { openPlayer });
    const rows = within(await table()).getAllByRole('row');
    const query = api.callsTo(TEAM_PATH)[0].url.searchParams;
    expect([query.get('team_id'), query.get('week')]).toEqual(['4', '5']);
    expect(within(rows[0]).getAllByRole('columnheader').map((cell) => cell.textContent))
      .toEqual(['Slot', 'Player', 'Opp', 'Proj', 'Pts', 'PPG', 'ROS', 'Value', 'Actions']);
    expect(rows.slice(1).map((row) => row.firstElementChild?.textContent))
      .toEqual(['QB', 'RB', 'RB', 'WR', 'WR', 'TE', 'FLEX', 'K', 'DST', 'Starters total', 'BN', 'BN', 'IR']);
    const mahomes = within(rowOf('Patrick Mahomes')).getAllByRole('cell').map((cell) => cell.textContent);
    expect(mahomes.slice(2, 8)).toEqual(['MIN', '21.30', '—', '15.10', '13.80', '31.5']);
    expect(rowOf('Starters total')).toHaveTextContent('Starters total118.40—');
    expect(rowOf('James Cook')).toHaveTextContent('BYE');
    expect(within(rowOf('Chris Olave')).getByText('Out')).toBeInTheDocument();
    expect(screen.getByText('Best lineup (auto)')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reset to best lineup' })).not.toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Lineup warnings' })).not.toBeInTheDocument();     // a bye on the bench is fine

    await user.click(screen.getByRole('button', { name: 'Patrick Mahomes' }));
    expect(openPlayer).toHaveBeenCalledWith(MAHOMES.id);
  });

  it('shows empty starting slots and warns about starters who are out or on bye', async () => {
    const results = team().results.filter((row) => row.player.id !== KELCE.id)
      .map((row) => (row.player.id === MAHOMES.id ? rosterRow(MAHOMES, 'QB', { bye: true, opponent: 'BYE', projection: 0 }) : row));
    renderTeam(team({ results }));
    await table();
    expect(rowOf('TE')).toHaveTextContent('TEEmpty');
    const warnings = within(screen.getByRole('list', { name: 'Lineup warnings' })).getAllByRole('listitem').map((item) => item.textContent);
    expect(warnings).toEqual(['Patrick Mahomes (QB) is on bye.', 'The TE slot is empty.']);
    expect(rowOf('Patrick Mahomes')).toHaveClass('replay-roster__row--warn');
  });

  it('shows another team or a past week read-only, with real points', async () => {
    const past = team({ team: teamRef(OPPONENT_TEAM_ID), week: 3, played: true, actual_total: 121.46,
      results: team().results.filter((row) => row.starting).map((row) => ({ ...row, points: 12.5 })) });
    const api = mockFetch([META_ROUTE, { path: TEAM_PATH, reply: (request) => json(request.url.searchParams.get('team_id') === '9' ? past : team()) }]);
    renderWithProviders(<TeamView {...viewProps()} />);
    const user = userEvent.setup();
    await table();
    await user.selectOptions(screen.getByLabelText('Team'), String(OPPONENT_TEAM_ID));
    await user.selectOptions(screen.getByLabelText('Week'), '3');

    const shown = await screen.findByRole('table', { name: 'Sunday Scaries roster, week 3' });
    const last = api.callsTo(TEAM_PATH).at(-1)!.url.searchParams;
    expect([last.get('team_id'), last.get('week')]).toEqual(['9', '3']);
    expect(within(screen.getByLabelText('Week')).getAllByRole('option')).toHaveLength(5);          // never a week not reached
    expect(within(shown).queryByRole('columnheader', { name: 'Actions' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^(Move|Drop)/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/lineup/i, { selector: 'strong' })).not.toBeInTheDocument();
    expect(rowOf('Starters total')).toHaveTextContent('Starters total118.40121.46');
    expect(rowOf('Patrick Mahomes')).toHaveTextContent('12.50');
  });

  it('says so when the roster cannot be loaded', async () => {
    mockFetch([META_ROUTE, { path: TEAM_PATH, reply: json({ detail: 'Week 9 can\'t be shown: this league is in week 5.' }, 422) }]);
    renderWithProviders(<TeamView {...viewProps()} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Week 9 can\'t be shown: this league is in week 5.');
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });
});

describe('TeamView matchups and played weeks', () => {
  it("shows how the opponent's defense rates against the player, in words", async () => {
    const results = team().results.map((row) => (row.player.id === MAHOMES.id ? { ...row, defense: defenseRating() } : row));
    renderTeam(team({ results }));
    await table();
    const opp = within(rowOf('Patrick Mahomes')).getAllByRole('cell')[2];
    expect(opp).toHaveTextContent(/^MIN Easy · 3rd/);
    expect(within(opp).getByText('Easy · 3rd', { exact: false })).toHaveAttribute('title', expect.stringContaining('3rd of 32 in points allowed to QBs'));
  });

  it("shows a played week's bench with its points and total", async () => {
    renderTeam(playedTeam());
    await screen.findByRole('table', { name: 'Your Team roster, week 3' });
    expect(rowOf('James Cook')).toHaveTextContent('14.20');
    expect(rowOf('Bench total')).toHaveTextContent('Bench total19.00');
    expect(screen.queryByText(/bench wasn.t saved/)).not.toBeInTheDocument();
  });

  it('says the bench was not saved for a week played before the update', async () => {
    const old = playedTeam();
    renderTeam({ ...old, results: old.results.filter((row) => row.starting), bench_total: null, bench_recorded: false });
    await screen.findByRole('table', { name: 'Your Team roster, week 3' });
    expect(screen.queryByRole('row', { name: /Bench total/ })).not.toBeInTheDocument();
    expect(screen.getByText(/The bench wasn’t saved for weeks played before this update\./)).toBeInTheDocument();
  });
});

describe('TeamView drag and drop', () => {
  it('drags a bench player onto a legal starter and posts the same lineup as the Move buttons', async () => {
    const { api } = renderTeam(team(), {}, [{ method: 'POST', path: LINEUP_PATH, reply: lineupReply() }]);
    await table();
    const cook = rowOf('James Cook');
    expect(cook).toHaveAttribute('draggable', 'true');
    fireEvent.dragStart(cook);
    expect(cook).toHaveClass('replay-roster__row--moving');
    expect(rowOf('Isiah Pacheco')).toHaveClass('replay-roster__row--target');
    expect(rowOf('Patrick Mahomes')).toHaveClass('replay-roster__row--dim');          // illegal spots dim while dragging
    expect(rowOf('Chris Olave')).toHaveClass('replay-roster__row--dim');
    expect(screen.getByRole('status')).toHaveTextContent('Moving James Cook: choose a highlighted spot');

    const target = rowOf('Isiah Pacheco');
    expect(fireEvent.dragOver(target)).toBe(false);                                   // default prevented: a drop is allowed here
    fireEvent.drop(target);
    fireEvent.dragEnd(cook);
    await waitFor(() => expect(api.callsTo(LINEUP_PATH, 'POST')).toHaveLength(1));
    expect(api.callsTo(LINEUP_PATH, 'POST')[0].body).toEqual({
      starters: { QB: [101], RB: [102, 103], WR: [104, 105], TE: [106], FLEX: [COOK.id], K: [108], DST: [109] },
    });
    expect(rowOf('Patrick Mahomes')).not.toHaveClass('replay-roster__row--dim');
  });

  it('refuses a drop on an illegal spot and posts nothing', async () => {
    const { api } = renderTeam();
    await table();
    const cook = rowOf('James Cook');
    fireEvent.dragStart(cook);
    const mahomes = rowOf('Patrick Mahomes');
    expect(fireEvent.dragOver(mahomes)).toBe(true);                                   // not cancelled: the browser shows "no drop"
    fireEvent.drop(mahomes);
    fireEvent.dragEnd(cook);
    expect(api.callsTo(LINEUP_PATH, 'POST')).toHaveLength(0);
    expect(rowOf('Patrick Mahomes')).not.toHaveClass('replay-roster__row--dim');
    expect(screen.getByRole('button', { name: 'Move James Cook' })).toBeInTheDocument();
  });

  it('dims illegal spots in tap mode too, and Escape cancels a drag', async () => {
    const { user } = renderTeam();
    await table();
    await user.click(screen.getByRole('button', { name: 'Move James Cook' }));
    expect(rowOf('Patrick Mahomes')).toHaveClass('replay-roster__row--dim');
    await user.keyboard('{Escape}');
    expect(rowOf('Patrick Mahomes')).not.toHaveClass('replay-roster__row--dim');

    fireEvent.dragStart(rowOf('James Cook'));
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('button', { name: /^Swap/ })).not.toBeInTheDocument();
    expect(rowOf('Isiah Pacheco')).not.toHaveClass('replay-roster__row--target');
  });

  it("cannot drag on a roster the user can't change", async () => {
    renderTeam(team({ team: teamRef(OPPONENT_TEAM_ID) }));
    await screen.findByRole('table', { name: 'Sunday Scaries roster, week 5' });
    expect(rowOf('James Cook')).not.toHaveAttribute('draggable');
  });
});

describe('TeamView lineup editing', () => {
  it('moves a bench player into a starter\'s slot and posts the whole lineup', async () => {
    const { api, user } = renderTeam(team(), {}, [{ method: 'POST', path: LINEUP_PATH, reply: lineupReply() }]);
    await table();
    await user.click(screen.getByRole('button', { name: 'Move James Cook' }));

    expect(screen.getByRole('status')).toHaveTextContent('Moving James Cook: choose a highlighted spot, or press Escape to cancel.');
    expect(screen.getByRole('button', { name: 'Cancel moving James Cook' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getAllByRole('button', { name: /^Swap James Cook with/ }).map((button) => button.getAttribute('aria-label')))
      .toEqual(['Swap James Cook with Christian McCaffrey', 'Swap James Cook with Travis Etienne', 'Swap James Cook with Isiah Pacheco']);
    expect(rowOf('Isiah Pacheco')).toHaveClass('replay-roster__row--target');
    expect(within(rowOf('Patrick Mahomes')).queryByRole('button', { name: /Move|Swap|Drop/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Swap James Cook with Isiah Pacheco' }));
    expect(api.callsTo(LINEUP_PATH, 'POST')[0].body).toEqual({
      starters: { QB: [101], RB: [102, 103], WR: [104, 105], TE: [106], FLEX: [COOK.id], K: [108], DST: [109] },
    });
    expect(await screen.findByRole('button', { name: 'Move James Cook' })).toBeInTheDocument();     // the move is over
  });

  it('moves a player into an empty slot', async () => {
    const roster = team({ results: team().results.filter((row) => row.player.id !== PACHECO.id) });
    const { api, user } = renderTeam(roster, {}, [{ method: 'POST', path: LINEUP_PATH, reply: lineupReply(roster) }]);
    await table();
    await user.click(screen.getByRole('button', { name: 'Move Chris Olave' }));
    await user.click(screen.getByRole('button', { name: 'Move Chris Olave to the empty FLEX slot' }));
    expect(api.callsTo(LINEUP_PATH, 'POST')[0].body).toMatchObject({ starters: { WR: [104, 105], FLEX: [OLAVE.id] } });
  });

  it('cancels a move with Escape or by pressing the button again', async () => {
    const { api, user } = renderTeam();
    await table();
    await user.click(screen.getByRole('button', { name: 'Move James Cook' }));
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('button', { name: /^Swap/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Move James Cook' }));
    await user.click(screen.getByRole('button', { name: 'Cancel moving James Cook' }));
    expect(screen.getByRole('button', { name: 'Move James Cook' })).toHaveAttribute('aria-pressed', 'false');
    expect(api.callsTo(LINEUP_PATH, 'POST')).toHaveLength(0);
  });

  it('keeps the move when an open drawer takes the Escape', async () => {
    const { user } = renderTeam();
    await table();
    await user.click(screen.getByRole('button', { name: 'Move James Cook' }));
    const drawer = (event: KeyboardEvent) => event.preventDefault();       // what ReplayDrawer does with Escape
    window.addEventListener('keydown', drawer, true);
    try {
      await user.keyboard('{Escape}');
    } finally {
      window.removeEventListener('keydown', drawer, true);
    }
    expect(screen.getByRole('button', { name: 'Cancel moving James Cook' })).toBeInTheDocument();
  });

  it('resets a manual lineup to the best one', async () => {
    const { api, user } = renderTeam(team({ manual_lineup: true }), {}, [{ method: 'POST', path: LINEUP_PATH, reply: lineupReply() }]);
    await table();
    expect(screen.getByText('Lineup set by you')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Reset to best lineup' }));
    expect(api.callsTo(LINEUP_PATH, 'POST')[0].body).toEqual({ starters: null });
  });

  it('drops a player after a confirmation, showing the pending state on that button', async () => {
    let finish: (reply: MockReply) => void = () => undefined;
    const reply = () => new Promise<MockReply>((resolve) => { finish = resolve; });
    const { api, user } = renderTeam(team(), {}, [{ method: 'POST', path: DROP_PATH, reply }]);
    await table();
    await user.click(within(rowOf('Chris Olave')).getByRole('button', { name: 'Drop Chris Olave' }));
    expect(api.callsTo(DROP_PATH, 'POST')).toHaveLength(0);
    await user.click(screen.getByRole('button', { name: 'Yes, drop Chris Olave' }));
    expect(await within(rowOf('Chris Olave')).findByRole('button', { name: 'Dropping… Chris Olave' })).toBeInTheDocument();
    expect(api.callsTo(DROP_PATH, 'POST')[0].body).toEqual({ player_id: OLAVE.id });
    finish(json(actionResult()));
    expect(await within(rowOf('Chris Olave')).findByRole('button', { name: 'Drop Chris Olave' })).toBeInTheDocument();
    // The row's button may be gone after the reload: focus lands on the status line, which says what happened.
    const status = screen.getByRole('status');
    await expect.poll(() => status).toHaveFocus();
    expect(status).toHaveTextContent('Dropped Chris Olave.');
  });

  it('shows the server\'s message next to the lineup when a move is refused', async () => {
    const message = 'Isiah Pacheco is a RB and can\'t play in the WR slot.';
    const { user } = renderTeam(team(), { actError: message }, [{ method: 'POST', path: LINEUP_PATH, reply: json({ detail: message }, 422) }]);
    await table();
    expect(screen.queryByText(message)).not.toBeInTheDocument();          // someone else's failure is the hub's to show
    await user.click(screen.getByRole('button', { name: 'Move James Cook' }));
    await user.click(screen.getByRole('button', { name: 'Swap James Cook with Isiah Pacheco' }));
    expect(await screen.findByText(message)).toBeInTheDocument();
  });
});

describe('TeamView phase rules', () => {
  it('allows editing during waivers', async () => {
    renderTeam(team(), { game: summary({ phase: 'waivers', next_action: nextAction('wednesday') }) });
    await table();
    expect(screen.getByRole('button', { name: 'Move Patrick Mahomes' })).toBeEnabled();
    expect(within(rowOf('Patrick Mahomes')).getByRole('button', { name: 'Drop Patrick Mahomes' })).toBeEnabled();
  });

  it('is read-only once the season is over', async () => {
    const final = team({ played: true, actual_total: 130.2, results: team().results.filter((row) => row.starting) });
    renderTeam(final, { game: summary({ phase: 'complete', next_action: nextAction('done') }) });
    await table();
    expect(screen.queryByRole('button', { name: /^(Move|Drop|Reset)/ })).not.toBeInTheDocument();
    expect(screen.queryByText('Best lineup (auto)')).not.toBeInTheDocument();
  });

  it('disables every action while another one is in flight', async () => {
    // (the IR slot is empty and nobody is tagged, so no IR button is counted here)
    renderTeam(team({ manual_lineup: true }), { isBusy: true });
    await table();
    for (const button of screen.getAllByRole('button', { name: /^(Move|Drop|Reset)/ })) expect(button).toBeDisabled();
    expect(screen.getAllByRole('button', { name: /^(Move|Drop|Reset)/ })).toHaveLength(23);      // 11 players twice, and Reset
  });
});

const PLACE_PATH = `/api/replay/games/${GAME_ID}/ir/place`;
const ACTIVATE_PATH = `/api/replay/games/${GAME_ID}/ir/activate`;
const ON_IR = tagged(MOSTERT, 'IR', null, 'Knee');
const irRow = (player = ON_IR) => ({ ...playerRow(player, { out: true, projection: 0 }), slot: 'IR' as const, starting: false as const });
/** The user's team with Olave (bench) tagged `tag`, and `ir` in the IR slot. */
function injured(tag: 'O' | 'Q' | 'SUS' = 'O', ir: ReplayIrRow[] = []) {
  const results = team().results.map((row) => (row.player.id === OLAVE.id
    ? rosterRow(tagged(OLAVE, tag, 'DNP', 'Ankle'), 'BN', { out: tag !== 'Q', projection: 0 }) : row));
  return team({ results, ir });
}
const lastRow = async () => within(await table()).getAllByRole('row').at(-1)!;
/** The pressed button may leave with its row: focus lands on the lineup status line, which says what happened. */
async function expectStatus(text: string) {
  await expect.poll(() => screen.getByRole('status')).toHaveFocus();
  expect(screen.getByRole('status')).toHaveTextContent(text);
}

describe('TeamView injuries and IR', () => {
  it('shows an empty IR slot in the current week, and no IR section for a past week', async () => {
    renderTeam();
    expect(await lastRow()).toHaveTextContent(/^IREmpty$/);
    expect(screen.getByText(/A player tagged O, IR or PUP can go on IR, which frees his roster spot\./)).toBeInTheDocument();
  });

  it('has no IR section in a played week', async () => {
    renderTeam(team({ week: 3, played: true, ir: [] }));
    const rows = within(await screen.findByRole('table', { name: 'Your Team roster, week 3' })).getAllByRole('row');
    expect(rows.map((row) => row.firstElementChild?.textContent)).not.toContain('IR');
    expect(screen.queryByText(/can go on IR/)).not.toBeInTheDocument();
  });

  it('moves a player tagged O to an empty IR slot', async () => {
    const { api, user } = renderTeam(injured('O'), {}, [{ method: 'POST', path: PLACE_PATH, reply: json(actionResult()) }]);
    await table();
    expect(within(rowOf('Chris Olave')).getByText('O')).toBeInTheDocument();
    expect(within(rowOf('Chris Olave')).queryByText('Out')).not.toBeInTheDocument();         // the tag already says it
    await user.click(screen.getByRole('button', { name: 'Move to IR Chris Olave' }));
    expect(api.callsTo(PLACE_PATH, 'POST')[0].body).toEqual({ player_id: OLAVE.id });
    await expectStatus('Moved Chris Olave to IR.');
  });

  it('names the IR button by its visible text, pending text included', async () => {
    let finish: (reply: MockReply) => void = () => undefined;
    const reply = () => new Promise<MockReply>((resolve) => { finish = resolve; });
    const { user } = renderTeam(injured('O'), {}, [{ method: 'POST', path: PLACE_PATH, reply }]);
    await table();
    await user.click(screen.getByRole('button', { name: 'Move to IR Chris Olave' }));
    expect(await screen.findByRole('button', { name: 'Moving… Chris Olave' })).toBeInTheDocument();
    finish(json(actionResult()));
    await expectStatus('Moved Chris Olave to IR.');
  });

  it('swaps a tagged player with the one on IR', async () => {
    const { api, user } = renderTeam(injured('O', [irRow()]), {}, [{ method: 'POST', path: ACTIVATE_PATH, reply: json(actionResult()) }]);
    expect(await lastRow()).toHaveTextContent('Raheem Mostert');
    await user.click(screen.getByRole('button', { name: 'Swap to IR Chris Olave, activating Raheem Mostert' }));
    expect(api.callsTo(ACTIVATE_PATH, 'POST')[0].body).toEqual({ player_id: MOSTERT.id, swap: OLAVE.id });
    await expectStatus('Moved Chris Olave to IR and activated Raheem Mostert.');
  });

  it('offers no IR move for a suspended or questionable player', async () => {
    for (const tag of ['SUS', 'Q'] as const) {
      mockFetch([META_ROUTE, { path: TEAM_PATH, reply: json(injured(tag)) }]);
      const { unmount } = renderWithProviders(<TeamView {...viewProps()} />);
      await table();
      expect(screen.queryByRole('button', { name: /to IR/ })).not.toBeInTheDocument();
      unmount();
    }
  });

  it('activates at once when the roster has room', async () => {
    const { api, user } = renderTeam(injured('O', [irRow()]), {}, [{ method: 'POST', path: ACTIVATE_PATH, reply: json(actionResult()) }]);
    await table();
    expect(within(await lastRow()).queryByRole('button', { name: /^Move Raheem/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Activate Raheem Mostert' }));
    expect(api.callsTo(ACTIVATE_PATH, 'POST')[0].body).toEqual({ player_id: MOSTERT.id });
    await expectStatus('Activated Raheem Mostert.');
  });

  it('asks who to drop when the roster is full, and shows a refusal inside the dialog', async () => {
    const full = summary({ settings: { ...summary().settings, roster_size: 11 } });
    const refusal = 'Your Team can\'t drop Chris Olave: he isn\'t on the active roster.';
    let refuse = true;
    const reply = () => (refuse ? json({ detail: refusal }, 422) : json(actionResult()));
    const { api, user } = renderTeam(injured('O', [irRow()]), { game: full }, [{ method: 'POST', path: ACTIVATE_PATH, reply }]);
    await table();
    await user.click(screen.getByRole('button', { name: 'Activate Raheem Mostert' }));
    const dialog = screen.getByRole('dialog', { name: 'Activate Raheem Mostert' });
    expect(within(dialog).getByText('Injured reserve')).toBeInTheDocument();
    expect(dialog).toHaveTextContent('Your roster is full (11 players): pick who to drop to make room for Raheem Mostert. Least valuable first.');
    expect(within(dialog).queryByRole('radio', { name: /Don't drop anyone/ })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole('radio', { name: /Raheem Mostert/ })).not.toBeInTheDocument();
    expect(within(dialog).getAllByRole('radio')).toHaveLength(11);
    await user.click(within(dialog).getByRole('radio', { name: /Chris Olave/ }));
    await user.click(within(dialog).getByRole('button', { name: 'Activate and drop' }));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(refusal);

    refuse = false;
    await user.click(within(dialog).getByRole('button', { name: 'Activate and drop' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(api.callsTo(ACTIVATE_PATH, 'POST').map((call) => call.body)).toEqual([
      { player_id: MOSTERT.id, drop: OLAVE.id }, { player_id: MOSTERT.id, drop: OLAVE.id },
    ]);
    await expectStatus('Activated Raheem Mostert, dropping Chris Olave.');
  });

  it('drops a player from IR and says so', async () => {
    const { api, user } = renderTeam(injured('O', [irRow()]), {}, [{ method: 'POST', path: DROP_PATH, reply: json(actionResult()) }]);
    await table();
    await user.click(screen.getByRole('button', { name: 'Drop Raheem Mostert' }));
    await user.click(screen.getByRole('button', { name: 'Yes, drop Raheem Mostert' }));
    expect(api.callsTo(DROP_PATH, 'POST')[0].body).toEqual({ player_id: MOSTERT.id });
    expect(await screen.findByText(/Dropped Raheem Mostert\./)).toBeInTheDocument();
  });

  it('shows a CPU team\'s IR without actions', async () => {
    renderTeam(team({ team: teamRef(OPPONENT_TEAM_ID), ir: [irRow()] }));
    const row = within(await screen.findByRole('table', { name: 'Sunday Scaries roster, week 5' })).getAllByRole('row').at(-1)!;
    expect(row).toHaveTextContent('Raheem Mostert');
    expect(screen.queryByRole('button', { name: /Activate|Drop/ })).not.toBeInTheDocument();
  });

  it('warns while an IR player has lost his tag, and when the season has no injury reports', async () => {
    const blocked = summary({ ir_blocked: [MOSTERT], has_injuries: false });
    renderTeam(team({ ir: [irRow(MOSTERT)] }), { game: blocked });
    await table();
    expect(screen.getByText('Raheem Mostert is on IR but no longer has an O, IR or PUP tag. Activate or drop him before you add, '
      + 'claim or trade for players.')).toHaveAttribute('role', 'status');
    expect(screen.getByText(/Injury reports are not loaded for the 2023 season, so no tags are shown and the IR slot can.t be used\./))
      .toHaveAttribute('role', 'status');
  });

  it('warns about a questionable starter and marks his row', async () => {
    const results = team().results.map((row) => (row.player.id === ETIENNE.id
      ? rosterRow(tagged(ETIENNE, 'Q', 'LP', 'Hamstring'), 'RB', { opponent: 'BUF' }) : row));
    renderTeam(team({ results }));
    await table();
    const warnings = within(screen.getByRole('list', { name: 'Lineup warnings' })).getAllByRole('listitem').map((item) => item.textContent);
    expect(warnings).toEqual(['Travis Etienne (RB) is questionable (Hamstring).']);
    expect(rowOf('Travis Etienne')).toHaveClass('replay-roster__row--warn');
  });

  it('hides every IR button while a lineup move is in progress', async () => {
    const { user } = renderTeam(injured('O', [irRow()]));
    await table();
    await user.click(screen.getByRole('button', { name: 'Move James Cook' }));
    expect(screen.queryByRole('button', { name: /to IR|^Activate|^Drop/ })).not.toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(screen.getByRole('button', { name: 'Activate Raheem Mostert' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Swap to IR Chris Olave, activating Raheem Mostert' })).toBeInTheDocument();
  });

  it('keeps the IR warning off another team\'s roster', async () => {
    renderTeam(team({ team: teamRef(OPPONENT_TEAM_ID) }), { game: summary({ ir_blocked: [MOSTERT] }) });
    await screen.findByRole('table', { name: 'Sunday Scaries roster, week 5' });
    expect(screen.queryByText(/is on IR but no longer has/)).not.toBeInTheDocument();
  });

  it('keeps the no-injury-reports notice to the current week', async () => {
    renderTeam(team({ week: 3, played: true }), { game: summary({ has_injuries: false }) });
    await screen.findByRole('table', { name: 'Your Team roster, week 3' });
    expect(screen.queryByText(/Injury reports are not loaded/)).not.toBeInTheDocument();
  });
});
