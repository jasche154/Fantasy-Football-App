import { describe, expect, it } from 'vitest';
import { COOK, KELCE, MAHOMES, NACUA, PACHECO, rosterRow, summary, team, TUCKER } from './fixtures';
import { canSwap, lineupSpots, lineupWarnings, slotAccepts, swappedLineup, weekOptions } from './teamLineup';

const STARTERS = summary().settings.starters;
const spots = lineupSpots(team().results, STARTERS);
const spot = (name: string) => spots.find((item) => item.row?.player.name === name)!;

describe('lineup helpers', () => {
  it('lists weeks 1 to the current one', () => {
    expect(weekOptions(3)).toEqual([{ value: 1, label: 'Week 1' }, { value: 2, label: 'Week 2' }, { value: 3, label: 'Week 3' }]);
  });

  it('knows which positions a slot takes', () => {
    expect(['RB', 'WR', 'TE', 'QB', 'K', 'DST'].map((position) => slotAccepts('FLEX', position as 'RB'))).toEqual([true, true, true, false, false, false]);
    expect([slotAccepts('BN', 'DST'), slotAccepts('QB', 'QB'), slotAccepts('K', 'DST')]).toEqual([true, true, false]);
  });

  it('pads empty starting slots and puts the bench last', () => {
    const short = lineupSpots(team().results.filter((row) => row.player.id !== KELCE.id && row.slot !== 'RB'), STARTERS);
    expect(short.map((item) => `${item.slot}:${item.row?.player.name ?? 'Empty'}`)).toEqual([
      'QB:Patrick Mahomes', 'RB:Empty', 'RB:Empty', 'WR:Tyreek Hill', 'WR:Puka Nacua', 'TE:Empty', 'FLEX:Isiah Pacheco',
      'K:Justin Tucker', 'DST:San Francisco 49ers', 'BN:James Cook', 'BN:Chris Olave',
    ]);
    expect(lineupWarnings(short)).toEqual(['The RB slot is empty.', 'The RB slot is empty.', 'The TE slot is empty.']);
  });

  it('allows only swaps both players are eligible for', () => {
    const targets = (name: string) => spots.filter((item) => canSwap(spot(name), item)).map((item) => item.row?.player.name);
    expect(targets('James Cook')).toEqual(['Christian McCaffrey', 'Travis Etienne', 'Isiah Pacheco']);      // bench RB: the RB slots and FLEX
    expect(targets('Patrick Mahomes')).toEqual([]);                                                          // no other QB on the roster
    // An RB in FLEX: the RB slots (those RBs fit FLEX) and any bench RB/WR/TE, but not a WR slot.
    expect(targets('Isiah Pacheco')).toEqual(['Christian McCaffrey', 'Travis Etienne', 'James Cook', 'Chris Olave']);
    // A WR: only the bench WR. Not FLEX, because the RB there can't take his WR slot.
    expect(targets('Puka Nacua')).toEqual(['Chris Olave']);
  });

  it('builds the whole lineup after a swap, into a filled or an empty slot', () => {
    expect(swappedLineup(spots, spot('James Cook'), spot('Isiah Pacheco'))).toEqual({
      QB: [MAHOMES.id], RB: [102, 103], WR: [104, NACUA.id], TE: [KELCE.id], FLEX: [COOK.id], K: [TUCKER.id], DST: [109],
    });
    const noFlex = lineupSpots(team().results.filter((row) => row.player.id !== PACHECO.id), STARTERS);
    const empty = noFlex.find((item) => item.slot === 'FLEX')!;
    const cook = noFlex.find((item) => item.row?.player.id === COOK.id)!;
    expect(canSwap(cook, empty)).toBe(true);
    expect(swappedLineup(noFlex, cook, empty).FLEX).toEqual([COOK.id]);
    // A starter moved into an empty slot leaves his own slot empty.
    const etienne = noFlex.find((item) => item.row?.player.name === 'Travis Etienne')!;
    expect(swappedLineup(noFlex, etienne, empty)).toMatchObject({ RB: [102], FLEX: [103] });
  });

  it('warns about starters who are out or on bye, not about the bench', () => {
    const rows = team().results.map((row) => (row.player.id === MAHOMES.id ? rosterRow(MAHOMES, 'QB', { bye: true, opponent: 'BYE' })
      : row.player.id === KELCE.id ? rosterRow(KELCE, 'TE', { out: true }) : row));
    expect(lineupWarnings(lineupSpots(rows, STARTERS))).toEqual(['Patrick Mahomes (QB) is on bye.', 'Travis Kelce (TE) is out.']);
    expect(lineupWarnings(spots)).toEqual([]);
  });
});
