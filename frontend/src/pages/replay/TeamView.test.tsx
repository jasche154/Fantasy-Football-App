import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { ReplayTeam } from '../../api/types';
import { json, mockFetch, type MockReply, type MockRoute } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import {
  actionResult, COOK, GAME_ID, KELCE, MAHOMES, nextAction, OLAVE, OPPONENT_TEAM_ID, PACHECO, rosterRow, summary, team, teamRef, viewProps,
} from './fixtures';
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
      .toEqual(['QB', 'RB', 'RB', 'WR', 'WR', 'TE', 'FLEX', 'K', 'DST', 'Starters total', 'BN', 'BN']);
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
    renderTeam(team({ manual_lineup: true }), { isBusy: true });
    await table();
    for (const button of screen.getAllByRole('button', { name: /^(Move|Drop|Reset)/ })) expect(button).toBeDisabled();
    expect(screen.getAllByRole('button', { name: /^(Move|Drop|Reset)/ })).toHaveLength(23);      // 11 players twice, and Reset
  });
});
