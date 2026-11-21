import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { json, mockFetch } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { GAME_ID, MOSTERT, playerDetail, tagged, usage } from './fixtures';
import { PlayerDrawer } from './PlayerDrawer';

describe('PlayerDrawer usage', () => {
  it('shows each played week\'s usage, a dash for a bye or the current week, and his last 3 games in the header', async () => {
    const detail = playerDetail(MOSTERT, { usage: usage() });
    mockFetch([META_ROUTE, { path: `/api/replay/games/${GAME_ID}/players/${MOSTERT.id}`, reply: json(detail) }]);
    renderWithProviders(<PlayerDrawer gameId={GAME_ID} playerId={MOSTERT.id} version={0} onClose={() => undefined} />);

    const dialog = within(await screen.findByRole('dialog', { name: 'Raheem Mostert details' }));
    expect(await dialog.findByText('Last 3 games: 72% snaps, 6.3 targets (24% share), 0.7 carries a game')).toBeInTheDocument();
    const rows = within(dialog.getByRole('table')).getAllByRole('row');
    const usageOf = (index: number) => within(rows[index]).getAllByRole('cell').slice(-4).map((cell) => cell.textContent);
    expect(usageOf(1)).toEqual(['61%', '1.0', '5%', '10.0']);           // week 1
    expect(usageOf(3)).toEqual(['—', '—', '—', '—']);                  // bye
    expect(usageOf(5)).toEqual(['—', '—', '—', '—']);                  // not played yet
    expect(dialog.getByTitle('Share of offensive snaps, that week')).toHaveTextContent('Snap %');
  });

  it('has no usage line before he has played', async () => {
    mockFetch([META_ROUTE, { path: `/api/replay/games/${GAME_ID}/players/${MOSTERT.id}`, reply: json(playerDetail(MOSTERT)) }]);
    renderWithProviders(<PlayerDrawer gameId={GAME_ID} playerId={MOSTERT.id} version={0} onClose={() => undefined} />);
    expect(await screen.findByRole('table')).toBeInTheDocument();
    expect(screen.queryByText(/^Last \d games?:/)).not.toBeInTheDocument();
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
