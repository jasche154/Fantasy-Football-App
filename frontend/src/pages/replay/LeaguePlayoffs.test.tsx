import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { ReplayBracketGame, ReplayBracketPool, ReplayBracketSide, ReplayPlayoffs, ReplaySeed } from '../../api/types';
import { json, mockFetch, type MockReply } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { GAME_ID, summary, teamRef, USER_TEAM_ID, viewProps } from './fixtures';
import { LeaguePlayoffs } from './LeaguePlayoffs';

const PATH = `/api/replay/games/${GAME_ID}/playoffs`;
const SIX_TEAM_GAME = summary({ end_week: 17, settings: { ...summary().settings, playoff_teams: 6 } });

/** Seeds follow team order (team 4, the user, is seed 4). */
function seed(teamId: number): ReplaySeed {
  return { ...teamRef(teamId), seed: teamId };
}

function sideOf(teamId: number, points: number | null = null, projected: number | null = null): ReplayBracketSide {
  return { ...seed(teamId), points, projected };
}

function match(label: string, home: ReplayBracketSide | null, away: ReplayBracketSide | null, winner: number | null = null): ReplayBracketGame {
  return { label, home, away, winner };
}

const TBD = (label: string) => match(label, null, null);

function renderPlayoffs(reply: MockReply, game = SIX_TEAM_GAME) {
  mockFetch([META_ROUTE, { path: PATH, reply }]);
  renderWithProviders(<LeaguePlayoffs {...viewProps({ game })} />);
}

/** A round (or pool week) column: the list item under the heading that names it. */
function column(name: RegExp, scope: HTMLElement = document.body): HTMLElement {
  const item = within(scope).getByRole('heading', { name }).closest('li');
  expect(item).not.toBeNull();
  return item as HTMLElement;
}

function cards(scope: HTMLElement): HTMLElement[] {
  return within(scope).getAllByRole('listitem');
}

/** The week before the seeds lock: six-team bracket, two byes, both pools still open. */
const PROJECTED: ReplayPlayoffs = {
  weeks: [15, 16, 17], seeds_final: false, champion: null,
  title: [
    { week: 15, name: 'Quarter-finals', projected: true, byes: [seed(1), seed(2)],
      games: [match('Quarter-final', sideOf(3), sideOf(6)), match('Quarter-final', sideOf(4), sideOf(5))] },
    { week: 16, name: 'Semi-finals', projected: false, byes: [], games: [TBD('Semi-final'), TBD('Semi-final')] },
    { week: 17, name: 'Final', projected: false, byes: [], games: [TBD('Championship'), TBD('Third place')] },
  ],
  pools: [
    { name: 'Round-one losers', places: [5, 6], first_week: 16, size: 2, teams: null,
      weeks: [{ week: 16, known: false, games: [] }, { week: 17, known: false, games: [] }] },
    { name: 'Consolation', places: [7, 12], first_week: 15, size: 6, teams: [7, 8, 9, 10, 11, 12].map(seed),
      weeks: [15, 16, 17].map((week) => ({ week, known: false, games: [] })) },
  ],
};

/** A finished four-team bracket the user won, and the consolation pool with every week played. */
function finished(): ReplayPlayoffs {
  const pool: ReplayBracketPool = {
    name: 'Consolation', places: [5, 12], first_week: 15, size: 8, teams: [5, 6, 7, 8, 9, 10, 11, 12].map(seed),
    weeks: [
      { week: 15, known: true, games: [match('Consolation', sideOf(5, 101.2), sideOf(6, 88.4), 5), match('Consolation', sideOf(7, 77), sideOf(8, 93.1), 8)] },
      { week: 16, known: true, games: [match('Consolation', sideOf(5, 110), sideOf(8, 112.5), 8)] },
    ],
  };
  return {
    weeks: [15, 16], seeds_final: true, champion: teamRef(USER_TEAM_ID),
    title: [
      { week: 15, name: 'Semi-finals', projected: false, byes: [],
        games: [match('Semi-final', sideOf(1, 99.3), sideOf(4, 120.5), 4), match('Semi-final', sideOf(2, 131.06), sideOf(3, 104), 2)] },
      { week: 16, name: 'Final', projected: false, byes: [],
        games: [match('Championship', sideOf(2, 97.4), sideOf(4, 115.25), 4), match('Third place', sideOf(1, 90), sideOf(3, 101.7), 3)] },
    ],
    pools: [pool],
  };
}

describe('LeaguePlayoffs before the seeds lock', () => {
  it('projects round one from today\'s seeds, with the byes and every later slot still open', async () => {
    renderPlayoffs(json(PROJECTED));
    const title = await screen.findByRole('list', { name: 'Title bracket' });

    expect(screen.getByRole('heading', { level: 2, name: /Playoffs\s*Weeks 15–17/ })).toBeInTheDocument();
    expect(screen.getByText('The seeds lock after week 14. Round one below uses today’s standings.')).toBeInTheDocument();
    expect(screen.queryByText(/Champion:/)).not.toBeInTheDocument();

    const quarters = column(/Quarter-finals\s*Week 15\s*Projected/, title);
    const [bye1, bye2, first, second] = cards(quarters);
    expect(bye1).toHaveTextContent('Seed 1Gridiron GhostsBye');
    expect(bye2).toHaveTextContent('Seed 2Blitz BrigadeBye');
    expect(first).toHaveTextContent('Seed 3End Zone EliteSeed 6Pigskin Pros');
    expect(second).toHaveTextContent('Seed 4Your Team (you)Seed 5Hail Marys');
    expect(within(quarters).queryByText('won')).not.toBeInTheDocument();

    for (const name of [/Semi-finals\s*Week 16/, /Final\s*Week 17/]) {
      const round = column(name, title);
      expect(within(round).queryByText('Projected')).not.toBeInTheDocument();
      expect(within(round).getAllByText('To be decided')).toHaveLength(4);
    }
    const [championship, third] = cards(column(/Final\s*Week 17/, title));
    expect(championship).toHaveClass('bracket-game--final', 'bracket-game--tbd');
    expect(championship).toHaveTextContent(/^Championship/);
    expect(third).not.toHaveClass('bracket-game--final');
    expect(third).toHaveTextContent(/^Third place/);
  });

  it('highlights the user\'s game and nobody else\'s', async () => {
    renderPlayoffs(json(PROJECTED));
    const title = await screen.findByRole('list', { name: 'Title bracket' });
    const mine = title.querySelectorAll('.bracket-game--mine');
    expect(mine).toHaveLength(1);
    expect(mine[0]).toHaveTextContent('Your Team (you)');
  });

  it('explains pools whose teams or pairs aren\'t known yet', async () => {
    renderPlayoffs(json(PROJECTED));
    const title = await screen.findByRole('list', { name: 'Title bracket' });
    expect(title).toHaveAttribute('tabindex', '0');                         // three columns can overflow
    expect(screen.getByRole('list', { name: 'Consolation weeks' })).toHaveAttribute('tabindex', '0');
    expect(screen.getByRole('list', { name: 'Round-one losers weeks' })).not.toHaveAttribute('tabindex');
    const losers = await screen.findByRole('region', { name: '5th–6th place Round-one losers' });
    expect(within(losers).getByText('The 2 teams that lose in round one play on here from week 16.')).toBeInTheDocument();
    expect(within(column(/^Week 16$/, losers)).getByText('Paired once the teams are known.')).toBeInTheDocument();
    expect(within(column(/^Week 17$/, losers)).getByText('Paired from the pool’s results after week 16.')).toBeInTheDocument();

    const consolation = screen.getByRole('region', { name: '7th–12th place Consolation' });
    expect(within(consolation).getByText(/^Teams:/)).toHaveTextContent('Teams: Fourth and Long, Red Zone Rebels, Sunday Scaries, Goal Line Gang, Two Minute Drill, Waiver Wizards');
    expect(within(consolation).getAllByText(/^Paired/)).toHaveLength(3);
    expect(within(consolation).queryByRole('heading', { name: /^Week 14$/ })).not.toBeInTheDocument();
  });
});

describe('LeaguePlayoffs once the season is over', () => {
  it('marks every winner, crowns the champion and drops the provisional note', async () => {
    renderPlayoffs(json(finished()), summary({ phase: 'complete', week: 16 }));
    const title = await screen.findByRole('list', { name: 'Title bracket' });

    expect(screen.getByText(/Champion:/)).toHaveTextContent('Champion: Your Team (you)');
    expect(screen.getByText(/Champion:/)).toHaveClass('replay-playoffs__champion--mine');
    expect(screen.queryByText(/The seeds lock/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Each round is reseeded/)).not.toBeInTheDocument();
    expect(within(title).queryByText('Projected')).not.toBeInTheDocument();

    const [upset, chalk] = cards(column(/Semi-finals\s*Week 15/, title));
    const [loser, winner] = upset.querySelectorAll('.bracket-game__line');
    expect(winner).toHaveClass('is-winner');
    expect(winner).toHaveTextContent('Seed 4Your Team (you)120.50won');
    expect(loser).toHaveClass('is-loser');
    expect(loser).toHaveTextContent('Seed 1Gridiron Ghosts99.30');
    expect(within(chalk).getAllByText('won')).toHaveLength(1);
    expect(chalk.querySelector('.is-winner')).toHaveTextContent('Blitz Brigade131.06won');

    const [championship, third] = cards(column(/Final\s*Week 16/, title));
    expect(championship).toHaveClass('bracket-game--final', 'bracket-game--mine');
    expect(championship.querySelector('.is-winner')).toHaveTextContent('Your Team (you)115.25won');
    expect(third).toHaveTextContent(/^Third place/);
    expect(third.querySelector('.is-winner')).toHaveTextContent('End Zone Elite101.70won');
    expect(third).not.toHaveClass('bracket-game--mine');
  });

  it('shows each consolation week\'s games with their winners', async () => {
    renderPlayoffs(json(finished()), summary({ phase: 'complete', week: 16 }));
    const pool = await screen.findByRole('region', { name: '5th–12th place Consolation' });
    expect(screen.getByRole('list', { name: 'Title bracket' })).not.toHaveAttribute('tabindex');      // two rounds fit
    expect(within(pool).queryByText(/^Paired/)).not.toBeInTheDocument();
    expect(cards(column(/^Week 15$/, pool))).toHaveLength(2);
    const [late] = cards(column(/^Week 16$/, pool));
    expect(late.querySelector('.is-winner')).toHaveTextContent('Seed 8Red Zone Rebels112.50won');
    expect(late).not.toHaveClass('bracket-game--mine');
    expect(screen.getByText(/Final places in a pool go by that table after its last week/)).toBeInTheDocument();
  });

  it('says once the seeds are final but nobody has won yet that every round is reseeded', async () => {
    renderPlayoffs(json({ ...finished(), champion: null }));
    expect(await screen.findByText('Each round is reseeded: the best seed left plays the lowest. A tie goes to the higher seed.')).toBeInTheDocument();
  });
});

describe('LeaguePlayoffs links to each game\'s matchup', () => {
  const hrefs = (scope: HTMLElement) => within(scope).queryAllByRole('link').map((link) => link.getAttribute('href'));

  it('opens a played game on the Matchup tab, by week and team', async () => {
    renderPlayoffs(json(finished()), summary({ phase: 'complete', week: 16 }));
    const title = await screen.findByRole('list', { name: 'Title bracket' });
    expect(hrefs(column(/Semi-finals\s*Week 15/, title))).toEqual([
      `#/play?game=${GAME_ID}&view=matchup&week=15&team=1`, `#/play?game=${GAME_ID}&view=matchup&week=15&team=2`]);
    const pool = screen.getByRole('region', { name: '5th–12th place Consolation' });
    expect(hrefs(column(/^Week 16$/, pool))).toEqual([`#/play?game=${GAME_ID}&view=matchup&week=16&team=5`]);
    expect(within(title).getAllByRole('link')[0]).toHaveAccessibleName(/open the week 15 matchup$/);
  });

  it('links this week\'s games (their preview) but never a later week, a projected round or an open slot', async () => {
    const live = finished();
    renderPlayoffs(json({ ...live, champion: null }), summary({ week: 15 }));
    const title = await screen.findByRole('list', { name: 'Title bracket' });
    expect(hrefs(column(/Semi-finals\s*Week 15/, title))).toHaveLength(2);
    expect(hrefs(column(/Final\s*Week 16/, title))).toEqual([]);              // week 16 hasn't come
  });

  it('has no links while round one is only projected', async () => {
    renderPlayoffs(json(PROJECTED), summary({ ...SIX_TEAM_GAME, week: 15 }));
    const title = await screen.findByRole('list', { name: 'Title bracket' });
    expect(within(title).queryAllByRole('link')).toEqual([]);
  });
});

describe('LeaguePlayoffs errors', () => {
  it('says the bracket could not be loaded', async () => {
    renderPlayoffs(json({ detail: 'boom' }, 500));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Couldn\'t load the playoff bracket');
    expect(within(alert).getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });
});
