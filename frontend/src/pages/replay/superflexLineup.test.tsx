import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { json, mockFetch } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { GAME_ID, HILL, playerRef, rosterRow, summary, team, TUCKER, viewProps } from './fixtures';
import { canSwap, lineupSpots, lineupWarnings, slotAccepts, slotLabel } from './teamLineup';
import { TeamView } from './TeamView';

const ALLEN = playerRef(120, 'Josh Allen', 'QB', 'BUF');
const SUPERFLEX_GAME = summary({ settings: { ...summary().settings, starters: { ...summary().settings.starters, SUPERFLEX: 1 }, roster_size: 16 } });
const STARTERS = SUPERFLEX_GAME.settings.starters;

describe('superflex lineups', () => {
  it('takes a QB, RB, WR or TE in SFLX, never a kicker or defense', () => {
    expect((['QB', 'RB', 'WR', 'TE', 'K', 'DST'] as const).map((position) => slotAccepts('SUPERFLEX', position)))
      .toEqual([true, true, true, true, false, false]);
    expect(slotAccepts('FLEX', 'QB')).toBe(false);
    expect([slotLabel('SUPERFLEX'), slotLabel('FLEX'), slotLabel('QB')]).toEqual(['SFLX', 'FLEX', 'QB']);
  });

  it('pads an empty SFLX spot after FLEX and names it SFLX in warnings', () => {
    const spots = lineupSpots(team().results, STARTERS);
    expect(spots.filter((spot) => spot.slot !== 'BN').map((spot) => spot.slot))
      .toEqual(['QB', 'RB', 'RB', 'WR', 'WR', 'TE', 'FLEX', 'SUPERFLEX', 'K', 'DST']);
    expect(lineupWarnings(spots)).toContain('The SFLX slot is empty.');
  });

  it('lets a bench quarterback move into SFLX (a drag or the Move buttons) but not a kicker', () => {
    const rows = [...team().results, rosterRow(ALLEN, 'BN', { starting: false })];
    const spots = lineupSpots(rows, STARTERS);
    const superflex = spots.find((spot) => spot.slot === 'SUPERFLEX')!;
    const allen = spots.find((spot) => spot.row?.player.id === ALLEN.id)!;
    const kicker = spots.find((spot) => spot.row?.player.id === TUCKER.id)!;
    const receiver = spots.find((spot) => spot.row?.player.id === HILL.id)!;

    expect(canSwap(allen, superflex)).toBe(true);
    expect(canSwap(receiver, superflex)).toBe(true);
    expect(canSwap(kicker, superflex)).toBe(false);
  });

  it('shows the SFLX slot on the roster table', async () => {
    const roster = team({ results: [...team().results, rosterRow(ALLEN, 'SUPERFLEX', { projection: 20.1 })] });
    mockFetch([META_ROUTE, { path: `/api/replay/games/${GAME_ID}/team`, reply: json(roster) }]);
    renderWithProviders(<TeamView {...viewProps({ game: SUPERFLEX_GAME })} />);

    const table = await screen.findByRole('table', { name: 'Your Team roster, week 5' });
    const slots = within(table).getAllByRole('row').slice(1).map((row) => row.firstElementChild?.textContent);
    expect(slots.slice(0, 10)).toEqual(['QB', 'RB', 'RB', 'WR', 'WR', 'TE', 'FLEX', 'SFLX', 'K', 'DST']);
    expect(screen.getByRole('row', { name: /Josh Allen/ }).firstElementChild).toHaveTextContent('SFLX');
  });
});
