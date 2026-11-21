import { type FormEvent, useEffect, useId, useRef, useState } from 'react';
import { cancelReplayClaim, reorderReplayClaims, setReplayClaimBid } from '../../api/replay';
import type { ReplayClaim, ReplayClaims } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { Card } from '../../components/Card';
import { Skeleton } from '../../components/StatusViews';
import type { AsyncResult } from '../../hooks/useAsync';
import { parseBid } from './PlayersDropDialog';
import type { PlayerMoves } from './PlayersMoves';
import { ordinal, ReplayPlayerCell } from './shared';

interface ClaimListProps {
  gameId: number;
  data: ReplayClaims;
  moves: PlayerMoves;
  openPlayer: (playerId: number) => void;
}

interface BidEditorProps {
  gameId: number;
  claim: ReplayClaim;
  left: number;
  moves: PlayerMoves;
}

/** A pending FAAB claim's bid, saved with its own button or Enter; an edit not yet saved says so. */
function BidEditor({ gameId, claim, left, moves }: BidEditorProps) {
  const id = useId();
  const [text, setText] = useState(String(claim.bid));
  const bid = parseBid(text, left);
  const name = claim.add.player.name;
  const canSave = !moves.isBusy && bid !== null && bid !== claim.bid;
  const save = (event: FormEvent) => {
    event.preventDefault();
    if (canSave) void moves.run(bidKey(claim), () => setReplayClaimBid(gameId, claim.id, bid), `Bid for ${name} is now $${bid}.`, true);
  };
  return (
    <form className="replay-claims__bid" onSubmit={save}>
      <label htmlFor={id}>Bid ($){' '}<span className="sr-only">for {name}</span></label>
      <input id={id} className="input replay-bid__input" type="number" inputMode="numeric" min={0} max={left} step={1} value={text}
        aria-invalid={bid === null} aria-describedby={`${id}-hint`} onChange={(event) => setText(event.target.value)} />
      <button type="submit" className="btn btn--ghost btn--sm" disabled={!canSave}>
        {moves.pendingKey === bidKey(claim) ? 'Saving…' : 'Save bid'}{' '}<span className="sr-only">for {name}</span>
      </button>
      <span id={`${id}-hint`} className="hint">
        {bid === null ? `Whole dollars, $0 to $${left}.` : bid !== claim.bid && `Not saved: the claim still bids $${claim.bid}.`}
      </span>
    </form>
  );
}

function ClaimList({ gameId, data, moves, openPlayer }: ClaimListProps) {
  const claims = data.results;
  const listRef = useRef<HTMLOListElement>(null);
  const moved = useRef<{ id: number; step: -1 | 1; index: number } | null>(null);

  // Reordering the keyed items can blur the pressed button, and at an edge it ends disabled: put focus back
  // on that claim once the new order is on screen.
  useEffect(() => {
    const target = moved.current;
    if (moves.isBusy || !target || claims[target.index]?.id !== target.id) return;
    moved.current = null;
    const item = listRef.current?.querySelector(`[data-claim="${target.id}"]`);
    const same = item?.querySelector<HTMLButtonElement>(`button[data-step="${target.step}"]:enabled`);
    (same ?? item?.querySelector<HTMLButtonElement>('.replay-claims__buttons button:enabled'))?.focus();
  }, [moves.isBusy, claims]);

  const move = async (index: number, step: -1 | 1) => {
    const ids = claims.map((claim) => claim.id);
    [ids[index], ids[index + step]] = [ids[index + step], ids[index]];
    moved.current = { id: claims[index].id, step, index: index + step };
    const sent = await moves.run(moveKey(claims[index], step), () => reorderReplayClaims(gameId, ids), 'Claim order updated.', true);
    if (!sent) moved.current = null;
  };
  return (
    <ol ref={listRef} className="replay-claims__list" aria-label="Your pending claims">
      {claims.map((claim, index) => {
        const name = claim.add.player.name;
        const about = <span className="sr-only">, claim for {name}</span>;
        const error = [moveKey(claim, -1), moveKey(claim, 1), cancelKey(claim), bidKey(claim)].map(moves.errorFor).find(Boolean);
        return (
          <li key={claim.id} className="replay-claims__item" data-claim={claim.id}>
            <span className="replay-claims__move">
              <ReplayPlayerCell player={claim.add.player} info={claim.add} onOpen={openPlayer} />
              <span className="hint">{claim.drop ? `Drop ${claim.drop.player.name}` : 'No drop'}</span>
            </span>
            {data.faab && <BidEditor gameId={gameId} claim={claim} left={data.faab.left} moves={moves} />}
            <span className="replay-claims__buttons">
              <button type="button" className="btn btn--ghost btn--sm" data-step={-1} disabled={moves.isBusy || index === 0} onClick={() => void move(index, -1)}>
                {moves.pendingKey === moveKey(claim, -1) ? 'Moving…' : 'Up'}{about}
              </button>
              <button type="button" className="btn btn--ghost btn--sm" data-step={1} disabled={moves.isBusy || index === claims.length - 1} onClick={() => void move(index, 1)}>
                {moves.pendingKey === moveKey(claim, 1) ? 'Moving…' : 'Down'}{about}
              </button>
              <button type="button" className="btn btn--ghost btn--sm" disabled={moves.isBusy}
                onClick={() => void moves.run(cancelKey(claim), () => cancelReplayClaim(gameId, claim.id), `Claim for ${name} cancelled.`)}>
                {moves.pendingKey === cancelKey(claim) ? 'Cancelling…' : 'Cancel'}{' '}<span className="sr-only">claim for {name}</span>
              </button>
            </span>
            {/* The hub's alert announces it; this copy sits where the user is looking. */}
            {error && <p className="error-text replay-claims__error">{error}</p>}
          </li>
        );
      })}
    </ol>
  );
}

function moveKey(claim: ReplayClaim, step: -1 | 1): string {
  return `move:${claim.id}:${step}`;
}

function cancelKey(claim: ReplayClaim): string {
  return `cancel:${claim.id}`;
}

function bidKey(claim: ReplayClaim): string {
  return `bid:${claim.id}`;
}

/** How claims settle: by priority, or (FAAB) by the highest blind bid with priority breaking ties. */
function RuleText({ data }: { data: ReplayClaims }) {
  const place = data.priority !== null && <><strong>You are {ordinal(data.priority)} of {data.waiver_order.length}</strong> in the waiver order. </>;
  if (data.faab) {
    const budget = <strong>You have ${data.faab.left} of ${data.faab.budget} left.</strong>;
    if (data.phase === 'complete') return <p className="replay-claims__rule">{budget}</p>;
    return (
      <p className="replay-claims__rule">
        {budget} {place}
        On “Sim to Wednesday” each player goes to the highest bid; a tie goes to the team higher in the waiver order. The
        winning bid comes off that team&apos;s budget. Bids are blind: budgets are public, other teams&apos; bids are not.
      </p>
    );
  }
  return (
    <p className="replay-claims__rule">
      {place}
      On “Sim to Wednesday” claims are awarded in this order: a team that wins a player moves to the back of the line, and
      players nobody claims become free agents.
    </p>
  );
}

/** The waiver order; in FAAB leagues with each team's budget left. */
function WaiverOrder({ data }: { data: ReplayClaims }) {
  const rows = data.faab?.teams ?? data.waiver_order.map((team) => ({ team, left: null }));
  return (
    <ol className="replay-claims__order" aria-label={data.faab ? 'Waiver order and budgets left' : 'Waiver order'}>
      {rows.map(({ team, left }) => (
        <li key={team.team_id} className={team.is_user ? 'replay-claims__team replay-claims__team--mine' : 'replay-claims__team'}>
          {team.name}{team.is_user && ' (you)'}{left !== null && `: $${left}`}
        </li>
      ))}
    </ol>
  );
}

interface ClaimsPanelProps {
  gameId: number;
  claims: AsyncResult<ReplayClaims>;
  moves: PlayerMoves;
  openPlayer: (playerId: number) => void;
}

function emptyText(data: ReplayClaims): string {
  if (data.can_claim) return 'You have no claims in. Press Claim next to a player below.';
  return data.phase === 'complete' ? 'The season is over: no more claims.' : 'Claims open after the next week is played.';
}

/** The waiver order and the user's pending claims, in the order they will be tried. */
export function ClaimsPanel({ gameId, claims, moves, openPlayer }: ClaimsPanelProps) {
  return (
    <Card title="Waiver claims" className="replay-claims">
      <AsyncView state={claims} loading={<Skeleton lines={3} label="Loading waiver claims…" />} errorTitle="Couldn't load your waiver claims">
        {(data) => (
          <>
            <RuleText data={data} />
            <WaiverOrder data={data} />
            {data.results.length === 0
              ? <p className="hint">{emptyText(data)}</p>
              : (
                <>
                  <h3 className="section-title">{data.faab ? 'Your claims: highest bid tried first, this order breaks ties' : 'Your claims, tried from the top'}</h3>
                  <ClaimList gameId={gameId} data={data} moves={moves} openPlayer={openPlayer} />
                </>
              )}
          </>
        )}
      </AsyncView>
    </Card>
  );
}
