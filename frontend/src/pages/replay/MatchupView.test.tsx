import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { ReplayMatchup, ReplayScoreboard } from '../../api/types';
import { json, mockFetch, type MockRoute } from '../../test/mockFetch';
import { AppProviders } from '../../App';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import {
  COLLINS, GAME_ID, KELCE, MAHOMES, MOSTERT, OPPONENT_TEAM_ID, record, rosterRow, side, summary, team, teamRef, USER_TEAM_ID, viewProps,
} from './fixtures';
import { MatchupView } from './MatchupView';
import type { ReplayViewProps } from './shared';

const MATCHUP_PATH = `/api/replay/games/${GAME_ID}/matchup`;
const SCOREBOARD_PATH = `/api/replay/games/${GAME_ID}/scoreboard`;

/** Sunday Scaries (home) against the user (away), week 5, not played. */
function matchup(overrides: Partial<ReplayMatchup> = {}): ReplayMatchup {
  const home = team({ team: teamRef(OPPONENT_TEAM_ID), projected_total: 104.75, record: record({ wins: 2, losses: 2, rank: 6 }),
    results: [rosterRow(MOSTERT, 'RB', { projection: 11.1 }), rosterRow(COLLINS, 'BN')] });
  return { week: 5, played: false, playoff: false, label: null, winner: null, home, away: team(), ...overrides };
}

function scoreboard(overrides: Partial<ReplayScoreboard> = {}): ReplayScoreboard {
  return { week: 5, played: false, playoff: false, results: [
    { home: side(OPPONENT_TEAM_ID, { projected: 104.75 }), away: side(USER_TEAM_ID, { projected: 118.4 }), winner: null, label: null },
    { home: side(1, { projected: 99.1 }), away: side(2, { projected: 101.2 }), winner: null, label: null },
  ], ...overrides };
}

function renderMatchup(routes: MockRoute[] = [], props: Partial<ReplayViewProps> = {}) {
  const api = mockFetch([META_ROUTE, ...routes, { path: MATCHUP_PATH, reply: json(matchup()) }, { path: SCOREBOARD_PATH, reply: json(scoreboard()) }]);
  renderWithProviders(<MatchupView {...viewProps(props)} />);
  return { api, user: userEvent.setup() };
}

const faceOff = () => screen.getByRole('region', { name: 'Face-off' });

describe('MatchupView', () => {
  it('puts the user on the left with projections only before the week is played', async () => {
    const openPlayer = vi.fn();
    const { api, user } = renderMatchup([], { openPlayer });
    const [mine, theirs] = await screen.findAllByRole('table');
    expect(api.callsTo(MATCHUP_PATH)[0].url.search).toBe('?week=5');
    expect(mine).toHaveAccessibleName('Your Team lineup, week 5');
    expect(theirs).toHaveAccessibleName('Sunday Scaries lineup, week 5');
    expect(faceOff()).toHaveTextContent('Regular season · Week 5 · Not played yet');
    expect(faceOff()).toHaveTextContent('Your Team (you)3-1 · 2nd118.40Projected');
    expect(faceOff()).toHaveTextContent('Sunday Scaries2-2 · 6th104.75Projected');
    expect(within(faceOff()).queryByText('Winner')).not.toBeInTheDocument();

    const headers = (table: HTMLElement) => within(table).getAllByRole('columnheader').map((cell) => cell.textContent);
    // Same column order on both sides, so a stacked phone layout reads the same way twice.
    expect(headers(mine)).toEqual(['Slot', 'Player', 'Proj', 'Pts']);
    expect(headers(theirs)).toEqual(['Slot', 'Player', 'Proj', 'Pts']);
    const cells = (table: HTMLElement, row: number) => within(within(table).getAllByRole('row')[row]).getAllByRole('cell').map((cell) => cell.textContent);
    expect(cells(mine, 1)[0]).toBe('QB');
    expect(cells(mine, 1)[1]).toContain('Patrick Mahomes');
    expect(cells(mine, 1).slice(2)).toEqual(['21.30', '—']);
    expect(cells(theirs, 1)).toEqual(['QB', 'Empty', '—', '—']);                        // an empty slot still gets its row
    expect(cells(theirs, 2)[0]).toBe('RB');
    expect(within(mine).getAllByRole('row')).toHaveLength(12);                         // header, 9 starters, 2 on the bench
    expect(within(theirs).getAllByRole('row')).toHaveLength(11);

    await user.click(within(mine).getByRole('button', { name: 'Patrick Mahomes' }));
    expect(openPlayer).toHaveBeenCalledWith(MAHOMES.id);
  });

  it('shows real totals, the winner and the playoff label once the week is played', async () => {
    const played = matchup({ played: true, playoff: true, label: 'Semi-final', winner: USER_TEAM_ID,
      home: { ...matchup().home, played: true, actual_total: 98.2 },
      away: team({ played: true, actual_total: 121.46, results: [rosterRow(KELCE, 'TE', { points: 17.4 })] }) });
    renderMatchup([{ path: MATCHUP_PATH, reply: json(played) }]);
    await screen.findAllByRole('table');
    expect(faceOff()).toHaveTextContent('Semi-final · Week 5 · Final');
    expect(faceOff()).toHaveTextContent('Your Team (you)Winner3-1 · 2nd121.46Projected 118.40');
    expect(faceOff()).toHaveTextContent('Sunday Scaries2-2 · 6th98.20Projected 104.75');
    expect(within(faceOff()).getAllByText('Winner')).toHaveLength(1);
    expect(screen.getByRole('row', { name: /Travis Kelce/ })).toHaveTextContent('17.40');
  });

  it('calls a played game without a winner a tie', async () => {
    renderMatchup([{ path: MATCHUP_PATH, reply: json(matchup({ played: true })) }]);
    await screen.findAllByRole('table');
    expect(faceOff()).toHaveTextContent('Week 5 · Final, a tie');
  });

  it('lists the week\'s games and shows the one that is pressed', async () => {
    const other = matchup({ home: team({ team: teamRef(1) }), away: team({ team: teamRef(2) }) });
    const { api, user } = renderMatchup([{ path: MATCHUP_PATH, reply: (request) => json(request.url.searchParams.get('team_id') === '1' ? other : matchup()) }]);
    const board = await screen.findByRole('region', { name: 'Week 5 scoreboard' });
    expect(board).toHaveTextContent('Week 5 scoreboard Projected');
    const [mine, others] = within(board).getAllByRole('button');
    expect(mine).toHaveTextContent('Sunday Scaries104.75Your Team (you)118.40');
    expect(others).toHaveTextContent('Gridiron Ghosts99.10Blitz Brigade101.20');
    await screen.findAllByRole('table');
    expect(mine).toHaveAttribute('aria-pressed', 'true');

    await user.click(others);
    expect(await screen.findByRole('table', { name: 'Gridiron Ghosts lineup, week 5' })).toBeInTheDocument();
    expect(api.callsTo(MATCHUP_PATH).at(-1)!.url.search).toBe('?week=5&team_id=1');
    expect(others).toHaveAttribute('aria-pressed', 'true');
    expect(faceOff()).toHaveFocus();

    await user.click(mine);
    expect(await screen.findByRole('table', { name: 'Your Team lineup, week 5' })).toBeInTheDocument();
    expect(api.callsTo(MATCHUP_PATH).at(-1)!.url.search).toBe(`?week=5&team_id=${OPPONENT_TEAM_ID}`);
  });

  it('goes back to the user\'s game when the week moves on', async () => {
    const api = mockFetch([META_ROUTE, { path: MATCHUP_PATH, reply: json(matchup()) }, { path: SCOREBOARD_PATH, reply: json(scoreboard()) }]);
    const user = userEvent.setup();
    const { rerender } = renderWithProviders(<MatchupView {...viewProps()} />);
    const board = await screen.findByRole('region', { name: 'Week 5 scoreboard' });
    await user.click(within(board).getAllByRole('button')[1]);
    await vi.waitFor(() => expect(api.callsTo(MATCHUP_PATH).at(-1)!.url.search).toBe('?week=5&team_id=1'));

    rerender(<AppProviders><MatchupView {...viewProps({ game: summary({ week: 6 }) })} /></AppProviders>);
    await vi.waitFor(() => expect(api.callsTo(MATCHUP_PATH).at(-1)!.url.search).toBe('?week=6'));
  });

  it('shows final scores and the winner on a played scoreboard', async () => {
    const final = scoreboard({ played: true, results: [{ home: side(1, { points: 110.02 }), away: side(2, { points: 109.88 }), winner: 1, label: 'Championship' }] });
    renderMatchup([{ path: SCOREBOARD_PATH, reply: json(final) }]);
    const game = within(await screen.findByRole('region', { name: 'Week 5 scoreboard' })).getByRole('button');
    expect(game).toHaveTextContent('ChampionshipGridiron Ghosts (won)110.02Blitz Brigade109.88');
  });

  it('loads another week for both the game and the scoreboard, back on the user\'s game', async () => {
    const { api, user } = renderMatchup();
    await screen.findAllByRole('table');
    expect(within(screen.getByLabelText('Week')).getAllByRole('option')).toHaveLength(5);          // never a week not reached
    await user.selectOptions(screen.getByLabelText('Week'), '3');
    expect(api.callsTo(MATCHUP_PATH).at(-1)!.url.search).toBe('?week=3');
    expect(api.callsTo(SCOREBOARD_PATH).at(-1)!.url.search).toBe('?week=3');
  });

  it('keeps the scoreboard when the game cannot be shown', async () => {
    renderMatchup([{ path: MATCHUP_PATH, reply: json({ detail: 'There is no game for that team in week 5.' }, 422) }]);
    expect(await screen.findByRole('alert')).toHaveTextContent('There is no game for that team in week 5.');
    expect(within(screen.getByRole('region', { name: 'Week 5 scoreboard' })).getAllByRole('button')).toHaveLength(2);
  });

  it('says the user has no game this week (a playoff bye) and shows the first game of the week', async () => {
    const quarter = matchup({ playoff: true, label: 'Quarter-final', home: team({ team: teamRef(3) }), away: team({ team: teamRef(6) }) });
    renderMatchup([{ path: MATCHUP_PATH, reply: json(quarter) }]);
    expect(await screen.findByText(/No game this week for your team\./)).toBeInTheDocument();
    expect(faceOff()).toHaveTextContent('Quarter-final · Week 5 · Not played yet');
  });

  it("does not call another week's game the user's bye while this week loads", async () => {
    const earlier = matchup({ week: 4, home: team({ team: teamRef(3) }), away: team({ team: teamRef(6) }) });
    renderMatchup([{ path: MATCHUP_PATH, reply: json(earlier) }]);
    await screen.findAllByRole('table');
    expect(screen.queryByText(/No game this week for your team/)).not.toBeInTheDocument();
  });

  it('says so when the week has no games', async () => {
    renderMatchup([{ path: SCOREBOARD_PATH, reply: json(scoreboard({ results: [] })) }]);
    expect(await screen.findByText('No games this week.')).toBeInTheDocument();
  });
});
