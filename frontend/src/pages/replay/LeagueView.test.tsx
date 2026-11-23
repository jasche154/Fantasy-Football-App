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
    regular_season_weeks: 14, playoff_teams: 4, playoff_weeks: [15, 16], byes: 0, seeds_final: false, bracket: [], champion: null, ...overrides,
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

/** The scoreboard tile (a list item) that mentions `name`. */
function tile(list: HTMLElement, name: RegExp): HTMLElement {
  const found = within(list).getAllByRole('listitem').filter((item) => name.test(item.textContent ?? ''));
  expect(found).toHaveLength(1);
  return found[0];
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
    expect(mine).toHaveTextContent('4Your Team (you)3-1478.34431.10');
    expect(mine).toHaveClass('row--mine', 'replay-standings__cut');
    expect(within(table).getByRole('row', { name: /Hail Marys/ })).not.toHaveClass('replay-standings__cut');
    expect(within(table).getByRole('columnheader', { name: 'Seed' })).toBeInTheDocument();
    expect(screen.queryByText('In playoffs')).not.toBeInTheDocument();
    expect(screen.getByText(/the top 4 seeds play for the title in weeks 15-16\. Seeds lock after week 14\./)).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Playoff bracket' })).not.toBeInTheDocument();
  });

  it('shows each CPU\'s trading tendency under its name, never the user\'s', async () => {
    const base = standings();
    const results = base.results.map((row) => ({ ...row, tendency: row.team_id === 2 || row.is_user ? 'Loves running backs' : null }));
    renderLeague([{ path: `${PATH}/standings`, reply: json({ ...base, results }) }]);
    const table = await screen.findByRole('table', { name: 'League standings' });
    expect(within(table).getByRole('row', { name: /Blitz Brigade/ })).toHaveTextContent('Blitz BrigadeLoves running backs');
    expect(within(table).getAllByText('Loves running backs')).toHaveLength(1);
    expect(within(table).getByRole('row', { name: /Your Team/ }).querySelector('[data-team="4"]')).not.toBeNull();
  });

  it('marks the playoff teams once the seeds are final and names the playoff weeks and the byes', async () => {
    renderLeague([{ path: `${PATH}/standings`, reply: json(standings({
      regular_season_weeks: 14, playoff_teams: 6, playoff_weeks: [15, 16, 17], byes: 2, seeds_final: true,
      results: standings().results.map((row) => ({ ...row, in_playoffs: row.seed <= 6 })),
    })) }]);

    const table = await screen.findByRole('table', { name: 'League standings' });
    expect(within(table).getAllByText('In playoffs')).toHaveLength(6);
    expect(screen.getByText(/the top 6 seeds play for the title in weeks 15-17, and the top 2 skip the first round\. The seeds are final\./))
      .toBeInTheDocument();
  });

  it('opens on the Playoffs page once the regular season is over', async () => {
    renderLeague([{ path: `${PATH}/playoffs`, reply: json({ weeks: [15, 16], seeds_final: true, champion: null, title: [], pools: [] }) }],
      { game: summary({ week: 15 }) });
    expect(screen.getByRole('button', { name: 'Playoffs' })).toHaveAttribute('aria-pressed', 'true');
    expect(await screen.findByRole('list', { name: 'Title bracket' })).toBeInTheDocument();
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
        { week: 4, playoff: false, played: true, idle: false, opponent: teamRef(1), points: 121.46, opponent_points: 98.2, result: 'W' },
        { week: 5, playoff: false, played: false, idle: false, opponent: teamRef(OPPONENT_TEAM_ID), points: null, opponent_points: null, result: null },
        { week: 15, playoff: true, played: false, idle: true, opponent: null, points: null, opponent_points: null, result: null },
        { week: 16, playoff: true, played: false, idle: false, opponent: null, points: null, opponent_points: null, result: null },
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
    expect(rows[2]).toHaveTextContent('15Bye / no gamePlayoffs——');
    expect(rows[3]).toHaveTextContent('16To be decidedPlayoffs——');

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

    const now = await screen.findByRole('list', { name: 'Week 5 scoreboard' });
    expect(screen.getByText('Week 5. Not played yet: these are pre-game projections.')).toBeInTheDocument();
    expect(tile(now, /Your Team/)).toHaveTextContent('ProjectedYour Team (you)118.40Sunday Scaries104.75');
    expect(tile(now, /Your Team/)).toHaveClass('game-tile--mine');
    expect(tile(now, /Gridiron Ghosts/)).not.toHaveClass('game-tile--mine');
    const picker = screen.getByRole('combobox', { name: 'Week' });
    expect(within(picker).getAllByRole('option')).toHaveLength(16);
    expect(within(picker).getByRole('option', { name: 'Week 15 (playoffs)' })).toBeInTheDocument();

    await user.selectOptions(picker, 'Week 4');
    const past = await screen.findByRole('list', { name: 'Week 4 scoreboard' });
    expect(tile(past, /Your Team/)).toHaveTextContent('FinalYour Team (you)Won121.46Gridiron Ghosts98.20');

    await user.selectOptions(picker, 'Week 9');
    const ahead = await screen.findByRole('list', { name: 'Week 9 scoreboard' });
    expect(screen.getByText('Week 9. Not played yet: the pairings only.')).toBeInTheDocument();
    expect(tile(ahead, /Your Team/)).toHaveTextContent(/^UpcomingYour Team \(you\)Blitz Brigade$/);

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
