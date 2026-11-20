/** Replay League: lineup rules for the My Team and Matchup screens (mirrors backend/services/replay_season.py). */
import type { ReplayLineup, ReplayPosition, ReplayRosterRow, ReplaySettings, ReplaySlot } from '../../api/types';
import type { SelectOption } from '../../components/SelectField';

type StartingSlot = Exclude<ReplaySlot, 'BN'>;

/** The order the backend lists starters in (replay_state.SLOTS). */
const STARTING_SLOTS: readonly StartingSlot[] = ['QB', 'RB', 'WR', 'TE', 'FLEX', 'K', 'DST'];
const FLEX_POSITIONS: readonly ReplayPosition[] = ['RB', 'WR', 'TE'];

/** One line of a roster table: a slot and who is in it (null: a starting slot left empty). */
export interface LineupSpot {
  key: string;
  slot: ReplaySlot;
  row: ReplayRosterRow | null;
}

/** Weeks 1..current for a week picker; nothing later is ever shown. */
export function weekOptions(currentWeek: number): SelectOption[] {
  return Array.from({ length: currentWeek }, (_, index) => ({ value: index + 1, label: `Week ${index + 1}` }));
}

export function slotAccepts(slot: ReplaySlot, position: ReplayPosition): boolean {
  if (slot === 'BN') return true;
  return slot === 'FLEX' ? FLEX_POSITIONS.includes(position) : slot === position;
}

/** Starters in slot order, padded with empty spots up to the league's lineup, then the bench. */
export function lineupSpots(rows: readonly ReplayRosterRow[], starters: ReplaySettings['starters']): LineupSpot[] {
  const starting = STARTING_SLOTS.flatMap((slot) => {
    const filled = rows.filter((row) => row.starting && row.slot === slot);
    const count = Math.max(starters[slot] ?? 0, filled.length);
    return Array.from({ length: count }, (_, index): LineupSpot => ({ key: `${slot}-${index}`, slot, row: filled[index] ?? null }));
  });
  // Keys are positions, not players, so a row's button keeps the focus when the lineup changes under it.
  const bench = rows.filter((row) => !row.starting).map((row, index): LineupSpot => ({ key: `BN-${index}`, slot: 'BN', row }));
  return [...starting, ...bench];
}

/** Can the player in `from` trade places with `to` (another player, or an empty starting slot)? */
export function canSwap(from: LineupSpot, to: LineupSpot): boolean {
  if (!from.row || from.slot === to.slot) return false;
  return slotAccepts(to.slot, from.row.player.position) && (!to.row || slotAccepts(from.slot, to.row.player.position));
}

/** The whole lineup to POST once `from` and `to` have traded places. */
export function swappedLineup(spots: readonly LineupSpot[], from: LineupSpot, to: LineupSpot): ReplayLineup {
  const lineup: ReplayLineup = {};
  for (const spot of spots) {
    if (spot.slot === 'BN') continue;
    const row = spot.key === from.key ? to.row : spot.key === to.key ? from.row : spot.row;
    lineup[spot.slot] = [...(lineup[spot.slot] ?? []), ...(row ? [row.player.id] : [])];
  }
  return lineup;
}

/** What is wrong with the starting lineup: starters who are out or on bye, and empty slots. */
export function lineupWarnings(spots: readonly LineupSpot[]): string[] {
  return spots.filter((spot) => spot.slot !== 'BN' && (!spot.row || spot.row.bye || spot.row.out)).map((spot) => {
    if (!spot.row) return `The ${spot.slot} slot is empty.`;
    return `${spot.row.player.name} (${spot.slot}) is ${spot.row.bye ? 'on bye' : 'out'}.`;
  });
}
