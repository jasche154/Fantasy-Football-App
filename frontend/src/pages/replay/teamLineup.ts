/** Replay League: lineup rules for the My Team and Matchup screens (mirrors backend/services/replay_season.py). */
import type { ReplayInjuryTag, ReplayLineup, ReplayPosition, ReplayRosterRow, ReplaySettings, ReplaySlot } from '../../api/types';
import type { SelectOption } from '../../components/SelectField';
import { isHardTag } from './shared';

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

const TAG_WARNINGS: Record<ReplayInjuryTag, string> = {
  O: 'is out', IR: 'is on injured reserve', PUP: 'is on the PUP list', SUS: 'is suspended', Q: 'is questionable', D: 'is doubtful',
};

/** One starter's problem, or null: bye, then out (worded by a hard tag), then a Q or D tag. */
function starterWarning(row: ReplayRosterRow, slot: ReplaySlot): string | null {
  const who = `${row.player.name} (${slot})`;
  const { injury } = row.player;
  if (row.bye) return `${who} is on bye.`;
  if (row.out) return `${who} ${injury && isHardTag(injury) ? TAG_WARNINGS[injury.tag] : 'is out'}.`;
  if (injury && !isHardTag(injury)) return `${who} ${TAG_WARNINGS[injury.tag]}${injury.injury ? ` (${injury.injury})` : ''}.`;
  return null;
}

/** What is wrong with the starting lineup: empty slots, starters on bye, out, or tagged Q or D. */
export function lineupWarnings(spots: readonly LineupSpot[]): string[] {
  return spots.filter((spot) => spot.slot !== 'BN').flatMap((spot) => {
    const warning = spot.row ? starterWarning(spot.row, spot.slot) : `The ${spot.slot} slot is empty.`;
    return warning ? [warning] : [];
  });
}
