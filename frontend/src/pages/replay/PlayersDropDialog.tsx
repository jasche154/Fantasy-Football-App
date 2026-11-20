import { useState, type FormEvent } from 'react';
import type { ReplayPlayerRef, ReplaySummary, ReplayTeam, ReplayWireRow } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { Skeleton } from '../../components/StatusViews';
import type { AsyncResult } from '../../hooks/useAsync';
import { formatNumber, pluralize } from '../../lib/format';
import type { PlayerMoves } from './PlayersMoves';
import { ReplayDrawer, ReplayPlayerCell } from './shared';

/** The key of the move the dialog sends. */
export const DIALOG_KEY = 'dialog';

export interface DropTarget {
  row: ReplayWireRow;
  kind: 'add' | 'claim';
}

interface ChooserProps {
  target: DropTarget;
  game: ReplaySummary;
  roster: ReplayTeam;
  earlierNoDrop: number;
  moves: PlayerMoves;
  onConfirm: (drop: ReplayPlayerRef | null) => void;
  onClose: () => void;
}

/** The roster hint; for a claim, says when earlier no-drop claims would already fill the open spots. */
function rosterHint(filled: number, size: number, earlierNoDrop: number, isCovered: boolean): string {
  if (filled >= size) return `Your roster is full (${size} players): pick who to drop.`;
  if (!isCovered) return `You have ${filled} of ${size} roster spots filled, so a drop is optional.`;
  const open = size - filled;
  return `You have ${filled} of ${size} roster spots filled, but your earlier ${pluralize(earlierNoDrop, 'claim')} without a drop `
    + `would fill the open ${open === 1 ? 'spot' : 'spots'}. If ${earlierNoDrop === 1 ? 'that wins' : 'those win'}, `
    + 'this claim fails unless you pick a drop.';
}

function Chooser({ target, game, roster, earlierNoDrop, moves, onConfirm, onClose }: ChooserProps) {
  const size = game.settings.roster_size;
  const openSpots = size - roster.results.length;
  const hasRoom = openSpots > 0;
  // Still allowed without a drop: an earlier claim can lose to a team ahead in the waiver order.
  const isCovered = target.kind === 'claim' && hasRoom && openSpots - earlierNoDrop <= 0;
  const [picked, setPicked] = useState<number | 'none' | null>(null);
  const choice = picked ?? (hasRoom && !isCovered ? 'none' : null);
  const players = [...roster.results].sort((a, b) => a.value - b.value);
  const isClaim = target.kind === 'claim';
  const isPending = moves.pendingKey === DIALOG_KEY;
  const error = moves.errorFor(DIALOG_KEY);
  const idleLabel = isClaim ? 'Submit claim' : (choice === 'none' ? 'Add' : 'Add and drop');

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (choice === null) return;
    onConfirm(choice === 'none' ? null : (players.find((row) => row.player.id === choice)?.player ?? null));
  };

  return (
    <form onSubmit={submit}>
      <fieldset className="fieldset">
        <legend className="section-title">Drop who?</legend>
        <p className="hint">
          {rosterHint(roster.results.length, size, earlierNoDrop, isCovered)}
          {' '}Least valuable first.{isClaim && ' The drop only happens if the claim wins.'}
        </p>
        {hasRoom && (
          <label className="replay-drop__option">
            <input type="radio" name="replay-drop" checked={choice === 'none'} onChange={() => setPicked('none')} />
            <span>Don&apos;t drop anyone</span>
          </label>
        )}
        {players.map((row) => (
          <label key={row.player.id} className="replay-drop__option">
            <input type="radio" name="replay-drop" checked={choice === row.player.id} onChange={() => setPicked(row.player.id)} />
            <ReplayPlayerCell player={row.player} info={row} />
            <span className="replay-drop__value">Value {formatNumber(row.value)}</span>
          </label>
        ))}
      </fieldset>
      {error && <p className="error-text" role="alert">{error}</p>}
      <div className="replay-drop__actions">
        <button type="submit" className="btn btn--primary" disabled={moves.isBusy || choice === null}>
          {isPending ? (isClaim ? 'Submitting…' : 'Adding…') : idleLabel}
        </button>
        <button type="button" className="btn btn--ghost" onClick={onClose}>Cancel</button>
      </div>
    </form>
  );
}

interface PlayersDropDialogProps {
  target: DropTarget;
  game: ReplaySummary;
  /** The user's roster this week. */
  team: AsyncResult<ReplayTeam>;
  /** The user's pending claims without a drop (0 when unknown). */
  earlierNoDrop: number;
  moves: PlayerMoves;
  /** The player to drop, or null for none. */
  onConfirm: (drop: ReplayPlayerRef | null) => void;
  onClose: () => void;
}

/** "Drop who?" for an add or a waiver claim. */
export function PlayersDropDialog({ target, game, team, earlierNoDrop, moves, onConfirm, onClose }: PlayersDropDialogProps) {
  const isClaim = target.kind === 'claim';
  return (
    <ReplayDrawer eyebrow={isClaim ? 'Waiver claim' : 'Free agent'} title={`${isClaim ? 'Claim' : 'Add'} ${target.row.player.name}`} onClose={onClose}>
      <AsyncView state={team} loading={<Skeleton lines={6} label="Loading your roster…" />} errorTitle="Couldn't load your roster">
        {(roster) => <Chooser target={target} game={game} roster={roster} earlierNoDrop={earlierNoDrop} moves={moves} onConfirm={onConfirm} onClose={onClose} />}
      </AsyncView>
    </ReplayDrawer>
  );
}
