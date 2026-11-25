import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { json, mockFetch } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import type { ReplayPlayerDetail } from '../../api/types';
import { GAME_ID, MOSTERT, OPPONENT_TEAM_ID, playerDetail, tagged, teamRef, TUCKER, usage } from './fixtures';
import { advancedDetail, cardDetail } from './fixturesPlayers';
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
