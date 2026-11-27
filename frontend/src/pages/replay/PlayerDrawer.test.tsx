import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { json, mockFetch } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import type { ReplayBoxScore, ReplayPlayerDetail } from '../../api/types';
import { GAME_ID, MAHOMES, MOSTERT, OPPONENT_TEAM_ID, playerDetail, tagged, teamRef, TUCKER, usage } from './fixtures';
import { advancedDetail, boxLine, cardDetail } from './fixturesPlayers';
import { PlayerDrawer } from './PlayerDrawer';

const PLAYER = `/api/replay/games/${GAME_ID}/players/${MOSTERT.id}`;

function renderCard(detail: ReplayPlayerDetail) {
  mockFetch([META_ROUTE, { path: PLAYER, reply: json(detail) }]);
  renderWithProviders(<PlayerDrawer gameId={GAME_ID} playerId={MOSTERT.id} version={0} onClose={() => undefined} />);
}

const cellsOf = (row: HTMLElement) => within(row).getAllByRole('cell').map((cell) => cell.textContent);

describe('PlayerDrawer card', () => {
  it('shows the header and five stat tiles', async () => {
    renderCard(playerDetail(tagged(MOSTERT, 'Q'), { usage: usage(), news: { back: 6, text: 'Ankle: limited in practice, expected back week 6.' },
      owner: teamRef(OPPONENT_TEAM_ID), status: 'rostered' }));
    const dialog = within(await screen.findByRole('dialog', { name: 'Raheem Mostert details' }));
    expect(await dialog.findByText('Ankle: limited in practice, expected back week 6.')).toBeInTheDocument();
    expect(dialog.getByText(/Rostered by Sunday Scaries/)).toBeInTheDocument();
    expect(dialog.getAllByRole('term').map((term) => term.textContent)).toEqual(['Games', 'Points', 'Per game', 'Rest of season', 'Trade value']);
    expect(dialog.getAllByRole('definition').map((value) => value.textContent)).toEqual(['4', '60.40', '15.10', '13.80', '32']);
  });

  it('logs a quarterback\'s completions and attempts, a dash where a saved league never recorded them', async () => {
    const detail = cardDetail(MAHOMES);
    renderCard({ ...detail, weeks: detail.weeks.map((week) => (week.box
      ? { ...week, box: boxLine({ pass_cmp: week.week === 1 ? null : 20 + week.week, pass_att: week.week === 1 ? null : 30 + week.week, pass_yds: 250 }) }
      : week)) });
    const log = within(await screen.findByRole('table', { name: 'Patrick Mahomes game log' }));
    expect(log.getAllByRole('columnheader').map((header) => header.textContent))
      .toEqual(['Wk', 'Opp', 'Proj', 'Pts', 'Cmp', 'Att', 'PaYd', 'PaTD', 'INT', 'RuYd', 'RuTD']);
    const rows = log.getAllByRole('row');
    expect(cellsOf(rows[1]).slice(3, 6)).toEqual(['—', '—', '250']);
    expect(cellsOf(rows[2]).slice(3, 6)).toEqual(['22', '32', '250']);
  });

  it('logs box scores for played weeks only, with @ for away games', async () => {
    renderCard(cardDetail());
    const log = within(await screen.findByRole('table', { name: 'Raheem Mostert game log' }));
    expect(log.getAllByRole('columnheader').map((header) => header.textContent)).toEqual(['Wk', 'Opp', 'Proj', 'Pts', 'Car', 'RuYd', 'RuTD', 'Tgt', 'Rec', 'ReYd', 'ReTD']);
    const rows = log.getAllByRole('row');
    expect(cellsOf(rows[1])).toEqual(['DEN', '12.50', '11.00', '15', '51', '1', '4', '3', '21', '0']);
    expect(cellsOf(rows[2])[0]).toBe('@ DEN');
    expect(cellsOf(rows[3])).toEqual(['BYE', '0.00', '—', '—', '—', '—', '—', '—', '—', '—']);
    // Week 5 is still to play: its projection and matchup, no result.
    expect(cellsOf(rows[5])).toEqual(['DENTough · 29th Tough matchup: ranks 29th of 32 in points allowed to RBs (18.4 a game).', '12.50', '—', '—', '—', '—', '—', '—', '—', '—']);
    expect(within(rows[5]).getByText(/^Tough · 29th/)).toHaveClass('game-chip--tough');
  });

  it('shows the weeks ahead with an estimate, the matchup and what the ratings rest on', async () => {
    renderCard(cardDetail());
    const upcoming = within(await screen.findByRole('table', { name: 'Raheem Mostert upcoming weeks' }));
    const rows = upcoming.getAllByRole('row');
    expect(cellsOf(rows[1])[0]).toBe('@ NYJ');
    expect(within(rows[1]).getByText(/^Easy · 3rd/)).toHaveClass('game-chip--easy');
    expect(cellsOf(rows[1])[2]).toBe('15.90');
    expect(cellsOf(rows[2])).toEqual(['BYE', '', '—']);
    expect(cellsOf(rows[3])).toEqual(['KC', '', 'Out']);
    expect(screen.getByText(/Est\. is his rest-of-season rate adjusted for the opponent, not a site projection\./))
      .toHaveTextContent('Matchup ratings: 4 weeks this season, blended with last season.');
  });

  it('puts the game log and advanced stats in tabs that work with the arrow keys', async () => {
    renderCard(advancedDetail(MOSTERT));
    const log = await screen.findByRole('tab', { name: 'Game log' });
    const advanced = screen.getByRole('tab', { name: 'Advanced' });
    expect(screen.getByRole('tablist', { name: 'Raheem Mostert stats' })).toBeInTheDocument();
    expect(log).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tabpanel', { name: 'Game log' })).toContainElement(screen.getByRole('table', { name: 'Raheem Mostert game log' }));
    expect(screen.queryByRole('table', { name: 'Raheem Mostert usage by week' })).not.toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(log);
    await user.keyboard('{ArrowRight}');
    expect(advanced).toHaveFocus();
    expect(advanced).toHaveAttribute('aria-selected', 'true');
    expect(log).toHaveAttribute('tabindex', '-1');
    expect(screen.getByRole('table', { name: 'Raheem Mostert usage by week' })).toBeInTheDocument();
    expect(screen.queryByRole('table', { name: 'Raheem Mostert game log' })).not.toBeInTheDocument();
    await user.keyboard('{ArrowRight}');
    expect(log).toHaveFocus();
    await user.keyboard('{End}');
    expect(advanced).toHaveAttribute('aria-selected', 'true');
  });

  it('leaves out Upcoming after the last week, and box columns for a kicker', async () => {
    renderCard({ ...playerDetail(TUCKER), upcoming: [] });
    const log = within(await screen.findByRole('table', { name: 'Justin Tucker game log' }));
    expect(log.getAllByRole('columnheader').map((header) => header.textContent)).toEqual(['Wk', 'Opp', 'Proj', 'Pts']);
    expect(screen.queryByRole('heading', { name: 'Upcoming' })).not.toBeInTheDocument();
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();       // advanced stats are for skill players
  });

  it('says the season has not started during the draft', async () => {
    renderCard({ ...playerDetail(), weeks: [] });
    expect(await screen.findByText('The season has not started: these are his preseason numbers.')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Game log' })).not.toBeInTheDocument();
  });

  it('closes on Escape and gives focus back to the opener', async () => {
    mockFetch([META_ROUTE, { path: PLAYER, reply: json(cardDetail()) }]);
    function Host() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>Open card</button>
          {open && <PlayerDrawer gameId={GAME_ID} playerId={MOSTERT.id} version={0} onClose={() => setOpen(false)} />}
        </>
      );
    }
    renderWithProviders(<Host />);
    const user = userEvent.setup();
    const opener = screen.getByRole('button', { name: 'Open card' });
    await user.click(opener);
    expect(await screen.findByRole('button', { name: 'Close player details' })).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });
});

describe('PlayerDrawer injuries', () => {
  it('shows the tag with its full label in words, and each week\'s tag in the Opp cell', async () => {
    const detail = playerDetail(tagged(MOSTERT, 'IR', null, 'Knee'), { out: true });
    const weeks = detail.weeks.map((week) => (week.week === 4 ? { ...week, out: true, injury: { tag: 'O' as const, practice: 'DNP' as const, injury: 'Knee' } }
      : week.week === 2 ? { ...week, out: true, injury: { tag: 'Q' as const, practice: null, injury: null } } : week));
    mockFetch([META_ROUTE, { path: `/api/replay/games/${GAME_ID}/players/${MOSTERT.id}`, reply: json({ ...detail, weeks }) }]);
    renderWithProviders(<PlayerDrawer gameId={GAME_ID} playerId={MOSTERT.id} version={0} onClose={() => undefined} />);

    const dialog = await screen.findByRole('dialog', { name: 'Raheem Mostert details' });
    expect(await within(dialog).findByText('Injured reserve: Knee', { selector: 'p' })).toBeInTheDocument();
    expect(within(dialog).getByTitle('Injured reserve: Knee')).toHaveTextContent('IR');
    const rows = within(within(dialog).getByRole('table')).getAllByRole('row');
    // Week 4: tagged O (the sr-only label follows the code), so no second "Out" badge.
    expect(rows[4]).toHaveTextContent('4DEN OOut: Knee (did not practice)12.50');
    expect(within(rows[4]).queryByText('Out')).not.toBeInTheDocument();
    expect(within(rows[2]).getByText('Out')).toBeInTheDocument();     // week 2: Q without a projection reads Q, Out
    expect(within(rows[2]).getByTitle('Questionable')).toHaveTextContent('Q');
    expect(rows[1]).toHaveTextContent(/^1DEN12\.50/);                  // untagged
  });
});

describe('PlayerDrawer in a simulated league', () => {
  function renderSim(detail: ReplayPlayerDetail, simFrom: number | null) {
    mockFetch([META_ROUTE, { path: PLAYER, reply: json(detail) }]);
    renderWithProviders(<PlayerDrawer gameId={GAME_ID} playerId={MOSTERT.id} version={0} simFrom={simFrom} onClose={() => undefined} />);
  }

  it('marks simulated weeks in the game log and the weeks ahead, with a legend', async () => {
    renderSim(cardDetail(), 4);
    const log = within(await screen.findByRole('table', { name: 'Raheem Mostert game log' }));
    const weekCells = log.getAllByRole('rowheader').map((cell) => cell.textContent);
    expect(weekCells).toEqual(['1', '2', '3', '4 Sim (simulated)', '5 Sim (simulated)']);
    expect(log.getAllByTitle('Simulated week')).toHaveLength(2);
    const ahead = within(screen.getByRole('table', { name: 'Raheem Mostert upcoming weeks' }));
    expect(ahead.getAllByTitle('Simulated week')).toHaveLength(3);
    expect(screen.getAllByText('Sim marks a simulated week: from week 4 on, this season is simulated, not the real NFL results.')).toHaveLength(2);
  });

  it('marks nothing in a real replay', async () => {
    renderSim(cardDetail(), null);
    await screen.findByRole('table', { name: 'Raheem Mostert game log' });
    expect(screen.queryByTitle('Simulated week')).not.toBeInTheDocument();
    expect(screen.queryByText(/Sim marks/)).not.toBeInTheDocument();
  });

  it('lists the buzz newest first as rumors, never as an injury', async () => {
    renderSim({ ...cardDetail(), buzz: [{ week: 5, text: 'Named the starter' }, { week: 3, text: 'Beat reporters say his role is growing; expect more work.' }] }, 4);
    const news = within(await screen.findByRole('region', { name: 'News' }));
    expect(news.getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      'Week 5 Named the starter', 'Week 3 Beat reporters say his role is growing; expect more work.']);
    expect(news.getByText(/Rumors from around the league/)).toHaveTextContent('They are right only some of the time.');
    expect(screen.queryByText(/Injured|Out:/)).not.toBeInTheDocument();
  });

  it('has no News block without buzz', async () => {
    renderSim(cardDetail(), 4);
    await screen.findByRole('table', { name: 'Raheem Mostert game log' });
    expect(screen.queryByRole('region', { name: 'News' })).not.toBeInTheDocument();
  });
});

describe('PlayerDrawer box score', () => {
  const BOX = `/api/replay/games/${GAME_ID}/box`;
  const boxScore = (hasScore = true): ReplayBoxScore => ({
    week: 1,
    teams: [
      { team: 'DEN', home: false, score: hasScore ? 17 : null, players: [
        { id: 301, name: 'Russell Wilson', position: 'QB', points: 18.4,
          box: boxLine({ pass_cmp: 22, pass_att: 34, pass_yds: 250, pass_td: 2, pass_int: 1, carries: 3, rush_yds: 12, rush_td: 0, targets: 0, rec: 0, rec_yds: 0 }) },
        { id: 304, name: 'Old Save Backup', position: 'QB', points: 1.6,     // a league saved before attempts were kept
          box: boxLine({ pass_cmp: null, pass_att: null, pass_yds: 40, carries: 0, rush_yds: 0, rush_td: 0, targets: 0, rec: 0, rec_yds: 0 }) },
        { id: 302, name: 'Broncos Kicker', position: 'K', points: 8, box: null }] },
      { team: 'MIA', home: true, score: hasScore ? 20 : null, players: [
        { id: MOSTERT.id, name: 'Raheem Mostert', position: 'RB', points: 11, box: boxLine() },
        { id: 303, name: 'Miami D/ST', position: 'DST', points: 6, box: null }] },
    ],
  });

  function renderBox(reply: ReplayBoxScore) {
    const api = mockFetch([META_ROUTE, { path: PLAYER, reply: json(cardDetail()) }, { path: BOX, reply: json(reply) }]);
    renderWithProviders(<PlayerDrawer gameId={GAME_ID} playerId={MOSTERT.id} version={0} onClose={() => undefined} />);
    return { api, user: userEvent.setup() };
  }

  it('opens a played game from the log: the final, his team\'s tab first, every line; Back returns to the card', async () => {
    const { api, user } = renderBox(boxScore());
    await screen.findByRole('table', { name: 'Raheem Mostert game log' });
    expect(screen.queryByRole('button', { name: /Week 3 box score/ })).not.toBeInTheDocument();     // a bye
    expect(screen.queryByRole('button', { name: /Week 5 box score/ })).not.toBeInTheDocument();     // not played yet
    await user.click(screen.getByRole('button', { name: 'Week 1 box score, DEN' }));

    expect(await screen.findByText(/DEN 17 @ MIA 20/)).toHaveTextContent('DEN 17 @ MIA 20 · Final');
    expect(Object.fromEntries(api.callsTo(BOX)[0].url.searchParams)).toEqual({ week: '1', team: 'MIA' });
    expect(screen.getByRole('heading', { name: 'Week 1 box score' })).toHaveFocus();
    const tabs = screen.getAllByRole('tab');
    expect(tabs.map((tab) => [tab.textContent, tab.getAttribute('aria-selected')])).toEqual([['DEN', 'false'], ['MIA', 'true']]);
    expect(screen.getByRole('table', { name: 'Rushing, MIA' })).toHaveTextContent('Raheem Mostert15721');
    expect(within(screen.getByRole('table', { name: 'Fantasy points, MIA' })).getAllByRole('rowheader').map((cell) => cell.textContent))
      .toEqual(['Raheem Mostert', 'Miami D/ST']);
    expect(screen.queryByRole('table', { name: 'Passing, MIA' })).not.toBeInTheDocument();

    tabs[1].focus();
    await user.keyboard('{ArrowLeft}');
    expect(screen.getByRole('tab', { name: 'DEN' })).toHaveAttribute('aria-selected', 'true');
    const passing = within(screen.getByRole('table', { name: 'Passing, DEN' }));
    expect(passing.getAllByRole('columnheader').map((header) => header.textContent)).toEqual(['Player', 'C/ATT', 'YDS', 'TD', 'INT']);
    expect(passing.getAllByRole('row').slice(1).map((row) => row.textContent)).toEqual(['Russell Wilson22/3425021', 'Old Save Backup—4000']);
    expect(screen.getByRole('table', { name: 'Fantasy points, DEN' })).toHaveTextContent('Broncos Kicker');

    await user.click(screen.getByRole('button', { name: '← Back to Raheem Mostert' }));
    expect(screen.queryByRole('heading', { name: 'Week 1 box score' })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Week 1 box score, DEN' })).toHaveFocus());
  });

  it('shows the matchup without a score when the league has none', async () => {
    const { user } = renderBox(boxScore(false));
    await user.click(await screen.findByRole('button', { name: 'Week 2 box score, @ DEN' }));
    expect(await screen.findByText(/final score not available/)).toBeInTheDocument();
    expect(screen.getByText(/DEN @ MIA/)).toBeInTheDocument();
  });
});
