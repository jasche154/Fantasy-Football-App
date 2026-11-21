import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { ReplayEvent, ReplayPage, ReplaySchedule, ReplayScoreboard, ReplayStandings } from '../../api/types';
import { json, mockFetch, type MockReply, type MockRequest, type MockRoute } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { event, GAME_ID, MOSTERT, OPPONENT_TEAM_ID, record, side, summary, teamRef, TEAMS, USER_TEAM_ID, viewProps } from './fixtures';
import { LeagueView } from './LeagueView';
import type { ReplayViewProps } from './shared';

const PATH = `/api/replay/games/${GAME_ID}`;

/** Seeds 1..12 in team order, so the user (team 4) is the last seed inside the cut. */
function standings(overrides: Partial<ReplayStandings> = {}): ReplayStandings {
  return {
    results: TEAMS.map((team, index) => ({ ...team, ...record({ rank: index + 1 }), seed: index + 1, in_playoffs: index < 4 })),
    regular_season_weeks: 14, playoff_teams: 4, seeds_final: false, bracket: [], champion: null, ...overrides,
  };
}

function board(week: number, overrides: Partial<ReplayScoreboard> = {}): ReplayScoreboard {
  return {
    week, played: false, playoff: false,
    results: [
      { home: side(USER_TEAM_ID, { projected: 118.4 }), away: side(OPPONENT_TEAM_ID, { projected: 104.75 }), winner: null, label: null },
      { home: side(1), away: side(2), winner: null, label: null },
    ],
    ...overrides,
  };
}

function renderLeague(routes: MockRoute[], props: Partial<ReplayViewProps> = {}) {
  const api = mockFetch([META_ROUTE, ...routes]);
  renderWithProviders(<LeagueView {...viewProps(props)} />);
  return { api, user: userEvent.setup() };
}

async function openPage(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(screen.getByRole('button', { name }));
  expect(screen.getByRole('button', { name })).toHaveAttribute('aria-pressed', 'true');
}

describe('LeagueView standings', () => {
  it('lists the table with the user highlighted and the playoff cut, without calling anyone in early', async () => {
    renderLeague([{ path: `${PATH}/standings`, reply: json(standings()) }]);
    const table = await screen.findByRole('table', { name: 'League standings' });
    const mine = within(table).getByRole('row', { name: /Your Team/ });

    expect(screen.getByRole('button', { name: 'Standings' })).toHaveAttribute('aria-pressed', 'true');
    expect(mine).toHaveTextContent('4Your Teamyou3-1478.34431.10');
    expect(mine).toHaveClass('row--mine', 'replay-standings__cut');
    expect(within(table).getByRole('row', { name: /Hail Marys/ })).not.toHaveClass('replay-standings__cut');
    expect(within(table).getByRole('columnheader', { name: 'Seed' })).toBeInTheDocument();
    expect(screen.queryByText('In playoffs')).not.toBeInTheDocument();
    expect(screen.getByText(/the top 4 seeds play for the title\. Seeds lock after week 14\./)).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Playoff bracket' })).not.toBeInTheDocument();
  });

  it('marks the playoff teams once the seeds are final and shows the bracket with projections, scores and the champion', async () => {
    const semis = board(15, { played: true, playoff: true, results: [
      { home: side(1, { points: 120.5 }), away: side(USER_TEAM_ID, { points: 131.02 }), winner: USER_TEAM_ID, label: 'Semi-final' },
      { home: side(2, { points: 99.1 }), away: side(3, { points: 98.4 }), winner: 2, label: 'Semi-final' },
      { home: side(5, { points: 80 }), away: side(6, { points: 70 }), winner: 5, label: 'Consolation' },
    ] });
    const final = board(16, { playoff: true, results: [
      { home: side(USER_TEAM_ID, { projected: 111.11 }), away: side(2, { projected: 109.9 }), winner: null, label: 'Championship' },
      { home: side(1, { projected: 101 }), away: side(3, { projected: 100 }), winner: null, label: 'Third place' },
    ] });
    renderLeague([{ path: `${PATH}/standings`, reply: json(standings({ seeds_final: true, bracket: [semis, final], champion: teamRef(USER_TEAM_ID) })) }]);

    const table = await screen.findByRole('table', { name: 'League standings' });
    expect(within(table).getAllByText('In playoffs')).toHaveLength(4);
    expect(within(table).getByRole('row', { name: /Hail Marys/ })).not.toHaveTextContent('In playoffs');
    expect(screen.getByText(/The seeds are final\./)).toBeInTheDocument();

    const bracket = screen.getByRole('region', { name: 'Playoff bracket' });
    expect(within(bracket).getByText(/Champion:/)).toHaveTextContent('Champion: Your Team');
    const played = within(bracket).getByRole('table', { name: 'Week 15 playoff games' });
    expect(within(played).getByRole('row', { name: /Your Team/ })).toHaveTextContent('Semi-finalGridiron Ghosts120.50Your TeamWon131.02');
    expect(within(played).queryByText('Consolation')).not.toBeInTheDocument();
    const ahead = within(bracket).getByRole('table', { name: 'Week 16 playoff games' });
    expect(within(ahead).getAllByRole('columnheader', { name: 'Proj' })).toHaveLength(2);
    expect(within(ahead).getByRole('row', { name: /Championship/ })).toHaveTextContent('ChampionshipYour Team111.11Blitz Brigade109.90');
    expect(within(ahead).queryByText('Won')).not.toBeInTheDocument();
  });

  it('shows the finish once the season is over', async () => {
    const done = standings();
    renderLeague([{ path: `${PATH}/standings`, reply: json({ ...done, results: done.results.map((row) => ({ ...row, place: row.seed })) }) }]);
    expect(await screen.findByRole('columnheader', { name: 'Finish' })).toBeInTheDocument();
  });

  it('says so when the standings cannot be loaded', async () => {
    renderLeague([{ path: `${PATH}/standings`, reply: json({ detail: 'Season data is not prepared.' }, 503) }]);
    expect(await screen.findByRole('alert')).toHaveTextContent('Season data is not prepared.');
  });
});

describe('LeagueView schedule', () => {
  function scheduleReply({ url }: MockRequest): MockReply {
    const body: ReplaySchedule = {
      team: teamRef(Number(url.searchParams.get('team_id'))),
      results: [
        { week: 4, playoff: false, played: true, opponent: teamRef(1), points: 121.46, opponent_points: 98.2, result: 'W' },
        { week: 5, playoff: false, played: false, opponent: teamRef(OPPONENT_TEAM_ID), points: null, opponent_points: null, result: null },
        { week: 15, playoff: true, played: false, opponent: null, points: null, opponent_points: null, result: null },
      ],
    };
    return json(body);
  }

  it('shows results for played weeks only, marks the playoffs and follows the team picker', async () => {
    const { api, user } = renderLeague([{ path: `${PATH}/standings`, reply: json(standings()) }, { path: `${PATH}/schedule`, reply: scheduleReply }]);
    await openPage(user, 'Schedule');

    const table = await screen.findByRole('table', { name: 'Your Team schedule' });
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows[0]).toHaveTextContent('4Gridiron GhostsW121.46 – 98.20');
    expect(rows[1]).toHaveTextContent('5Sunday Scaries——');
    expect(rows[2]).toHaveTextContent('15To be decidedPlayoffs——');

    await user.selectOptions(screen.getByRole('combobox', { name: 'Team' }), 'Hail Marys');
    expect(await screen.findByRole('table', { name: 'Hail Marys schedule' })).toBeInTheDocument();
    expect(api.callsTo(`${PATH}/schedule`).map((call) => call.url.searchParams.get('team_id'))).toEqual(['4', '5']);
  });
});

describe('LeagueView scoreboard', () => {
  function scoreboardReply({ url }: MockRequest): MockReply {
    const week = Number(url.searchParams.get('week'));
    if (week === 4) {
      return json(board(4, { played: true, results: [
        { home: side(USER_TEAM_ID, { points: 121.46 }), away: side(1, { points: 98.2 }), winner: USER_TEAM_ID, label: null },
      ] }));
    }
    if (week === 5) return json(board(5));
    if (week === 16) return json(board(16, { playoff: true, results: [] }));
    return json(board(week, { results: [{ home: side(USER_TEAM_ID, { projected: null }), away: side(2, { projected: null }), winner: null, label: null }] }));
  }

  it('shows projections this week, results before it and only the pairings ahead', async () => {
    const { api, user } = renderLeague([{ path: `${PATH}/standings`, reply: json(standings()) }, { path: `${PATH}/scoreboard`, reply: scoreboardReply }]);
    await openPage(user, 'Scoreboard');

    const now = await screen.findByRole('table', { name: 'Week 5 scoreboard' });
    expect(screen.getByText('Week 5. Not played yet: these are pre-game projections.')).toBeInTheDocument();
    expect(within(now).getByRole('row', { name: /Your Team/ })).toHaveTextContent('Your Team118.40Sunday Scaries104.75');
    expect(within(now).getByRole('row', { name: /Your Team/ })).toHaveClass('row--mine');
    const picker = screen.getByRole('combobox', { name: 'Week' });
    expect(within(picker).getAllByRole('option')).toHaveLength(16);
    expect(within(picker).getByRole('option', { name: 'Week 15 (playoffs)' })).toBeInTheDocument();

    await user.selectOptions(picker, 'Week 4');
    const past = await screen.findByRole('table', { name: 'Week 4 scoreboard' });
    expect(within(past).getByRole('row', { name: /Your Team/ })).toHaveTextContent('Your TeamWon121.46Gridiron Ghosts98.20');

    await user.selectOptions(picker, 'Week 9');
    const ahead = await screen.findByRole('table', { name: 'Week 9 scoreboard' });
    expect(screen.getByText('Week 9. Not played yet: the pairings only.')).toBeInTheDocument();
    expect(within(ahead).getByRole('row', { name: /Your Team/ })).toHaveTextContent(/^Your TeamBlitz Brigade$/);
    expect(within(ahead).queryByRole('columnheader', { name: /Proj|Pts/ })).not.toBeInTheDocument();

    await user.selectOptions(picker, 'Week 16 (playoffs)');
    expect(await screen.findByText('No games set yet')).toBeInTheDocument();
    expect(api.callsTo(`${PATH}/scoreboard`).map((call) => call.url.searchParams.get('week'))).toEqual(['5', '4', '9', '16']);
  });
});

describe('LeagueView activity', () => {
  function activityReply({ url }: MockRequest): MockReply {
    const offset = Number(url.searchParams.get('offset'));
    const body: ReplayPage<ReplayEvent> = {
      total: 26, limit: 25, offset,
      results: offset === 0
        ? [event(90, 'add', 'Your Team added Raheem Mostert', { team: teamRef(USER_TEAM_ID), players: [MOSTERT] }),
          event(89, 'result', 'Gridiron Ghosts beat Blitz Brigade 110.02-109.88', { week: 4, team: teamRef(1) })]
        : [event(1, 'draft', 'Gridiron Ghosts drafted Raheem Mostert', { week: 0, team: teamRef(1), players: [MOSTERT] })],
    };
    return json(body);
  }

  it('lists the log newest first, filters by kind, pages, and opens a player', async () => {
    const openPlayer = vi.fn();
    const { api, user } = renderLeague(
      [{ path: `${PATH}/standings`, reply: json(standings()) }, { path: `${PATH}/activity`, reply: activityReply }], { openPlayer });
    await openPage(user, 'Activity');

    const log = await screen.findByRole('list', { name: 'League activity' });
    const lines = within(log).getAllByRole('listitem');
    expect(lines[0]).toHaveTextContent('Wk 5Your Team added Raheem Mostert');
    expect(lines[0]).toHaveClass('replay-events__item--mine');
    expect(lines[1]).toHaveTextContent('Wk 4Gridiron Ghosts beat Blitz Brigade 110.02-109.88');
    expect(screen.getByText('1–2 of 26')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Newer' })).toBeDisabled();

    await user.click(within(log).getByRole('button', { name: 'Raheem Mostert' }));
    expect(openPlayer).toHaveBeenCalledWith(MOSTERT.id);

    await user.click(screen.getByRole('button', { name: 'Older' }));
    expect(await screen.findByText('26–26 of 26')).toBeInTheDocument();
    expect(screen.getByText('Draft')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Older' })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: 'Adds/Drops' }));
    expect(await screen.findByText('1–2 of 26')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Adds/Drops' })).toHaveAttribute('aria-pressed', 'true');
    const queries = api.callsTo(`${PATH}/activity`).map((call) => Object.fromEntries(call.url.searchParams));
    expect(queries).toEqual([
      { limit: '25', offset: '0' },
      { limit: '25', offset: '25' },
      { kind: 'add,drop,ir', limit: '25', offset: '0' },
    ]);
  });

  it('sends each filter\'s kinds and says when there is nothing', async () => {
    const empty: ReplayPage<ReplayEvent> = { total: 0, limit: 25, offset: 0, results: [] };
    const { api, user } = renderLeague([{ path: `${PATH}/standings`, reply: json(standings()) }, { path: `${PATH}/activity`, reply: json(empty) }],
      { game: summary() });
    await openPage(user, 'Activity');
    expect(await screen.findByText('Nothing here yet')).toBeInTheDocument();
    for (const name of ['Trades', 'Waiver claims', 'Results']) await user.click(screen.getByRole('button', { name }));
    await screen.findByText('Nothing here yet');
    expect(api.callsTo(`${PATH}/activity`).map((call) => call.url.searchParams.get('kind'))).toEqual([null, 'trade', 'claim', 'result,playoffs,champion']);
  });
});
