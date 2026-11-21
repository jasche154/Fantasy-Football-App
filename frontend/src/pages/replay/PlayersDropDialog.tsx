import { useId, useState, type FormEvent } from 'react';
import type { ReplayPlayerRef, ReplayPlayerRow, ReplaySummary, ReplayTeam } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { Skeleton } from '../../components/StatusViews';
import type { AsyncResult } from '../../hooks/useAsync';
import { formatNumber, pluralize } from '../../lib/format';
import { ReplayDrawer, ReplayPlayerCell, type KeyedAct } from './shared';

/** The key of the move the dialog sends. */
export const DIALOG_KEY = 'dialog';

export interface DropTarget {
  row: Pick<ReplayPlayerRow, 'player'>;
  /** `activate`: bringing a player back from IR onto a full roster (My Team). */
  kind: 'add' | 'claim' | 'activate';
}

type DialogMoves = Pick<KeyedAct, 'isBusy' | 'pendingKey' | 'errorFor'>;

/** A FAAB claim's bid: `left` is the user's budget left (null while unknown; the server still checks). */
export interface BidLimit {
  left: number | null;
}

/** A whole-dollar bid from 0 up to the budget left, or null. */
export function parseBid(text: string, left: number | null): number | null {
  if (!/^\d+$/.test(text.trim())) return null;
  const bid = Number(text);
  return left === null || bid <= left ? bid : null;
}

interface BidFieldProps {
  value: string;
  left: number | null;
  onChange: (value: string) => void;
}

function BidField({ value, left, onChange }: BidFieldProps) {
  const id = useId();
  return (
    <div className="field replay-bid">
      <label className="field__label" htmlFor={id}>Bid ($)</label>
      <input id={id} className="input replay-bid__input" type="number" inputMode="numeric" min={0} max={left ?? undefined} step={1}
        value={value} aria-invalid={parseBid(value, left) === null} aria-describedby={`${id}-hint`} onChange={(event) => onChange(event.target.value)} />
      <p id={`${id}-hint`} className="hint">
        {left === null ? 'Whole dollars.' : `Whole dollars, $0 to $${left}: you have $${left} left.`} Bids are blind; the highest wins.
      </p>
    </div>
  );
}

interface ChooserProps {
  target: DropTarget;
  game: ReplaySummary;
  roster: ReplayTeam;
  earlierNoDrop: number;
  moves: DialogMoves;
  /** Set for a claim in a FAAB league: the dialog asks for a bid. */
  bidding?: BidLimit;
  onConfirm: (drop: ReplayPlayerRef | null, bid?: number) => void;
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

/** The "Drop who?" form; My Team wraps it in its own drawer for an IR activation. */
export function Chooser({ target, game, roster, earlierNoDrop, moves, bidding, onConfirm, onClose }: ChooserProps) {
  const size = game.settings.roster_size;
  const openSpots = size - roster.results.length;
  const isActivate = target.kind === 'activate';
  // An activation only opens this on a full roster: he always needs a drop.
  const hasRoom = openSpots > 0 && !isActivate;
  // Still allowed without a drop: an earlier claim can lose to a team ahead in the waiver order.
  const isCovered = target.kind === 'claim' && hasRoom && openSpots - earlierNoDrop <= 0;
  const [picked, setPicked] = useState<number | 'none' | null>(null);
  const [bidText, setBidText] = useState('0');
  const bid = bidding ? parseBid(bidText, bidding.left) : undefined;
  const choice = picked ?? (hasRoom && !isCovered ? 'none' : null);
  const players = [...roster.results].sort((a, b) => a.value - b.value);
  const isClaim = target.kind === 'claim';
  const isPending = moves.pendingKey === DIALOG_KEY;
  const error = moves.errorFor(DIALOG_KEY);
  const idleLabel = isActivate ? 'Activate and drop' : isClaim ? 'Submit claim' : (choice === 'none' ? 'Add' : 'Add and drop');
  const pendingLabel = isActivate ? 'Activating…' : isClaim ? 'Submitting…' : 'Adding…';

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (choice === null || bid === null) return;
    onConfirm(choice === 'none' ? null : (players.find((row) => row.player.id === choice)?.player ?? null), bid);
  };

  return (
    <form onSubmit={submit}>
      {bidding && <BidField value={bidText} left={bidding.left} onChange={setBidText} />}
      <fieldset className="fieldset">
        <legend className="section-title">Drop who?</legend>
        <p className="hint">
          {isActivate
            ? `Your roster is full (${size} players): pick who to drop to make room for ${target.row.player.name}.`
            : rosterHint(roster.results.length, size, earlierNoDrop, isCovered)}
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
        <button type="submit" className="btn btn--primary" disabled={moves.isBusy || choice === null || bid === null}>
          {isPending ? pendingLabel : idleLabel}
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
  moves: DialogMoves;
  /** Set for a claim in a FAAB league. */
  bidding?: BidLimit;
  /** The player to drop, or null for none; the bid for a FAAB claim. */
  onConfirm: (drop: ReplayPlayerRef | null, bid?: number) => void;
  onClose: () => void;
}

/** "Drop who?" for an add or a waiver claim. */
export function PlayersDropDialog({ target, game, team, earlierNoDrop, moves, bidding, onConfirm, onClose }: PlayersDropDialogProps) {
  const isClaim = target.kind === 'claim';
  return (
    <ReplayDrawer eyebrow={isClaim ? 'Waiver claim' : 'Free agent'} title={`${isClaim ? 'Claim' : 'Add'} ${target.row.player.name}`} onClose={onClose}>
      <AsyncView state={team} loading={<Skeleton lines={6} label="Loading your roster…" />} errorTitle="Couldn't load your roster">
        {(roster) => <Chooser target={target} game={game} roster={roster} earlierNoDrop={earlierNoDrop} moves={moves} bidding={bidding} onConfirm={onConfirm} onClose={onClose} />}
      </AsyncView>
    </ReplayDrawer>
  );
}
