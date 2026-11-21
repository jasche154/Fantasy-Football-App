import { useEffect, useRef } from 'react';
import { cancelReplayClaim, reorderReplayClaims } from '../../api/replay';
import type { ReplayClaim, ReplayClaims } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { Card } from '../../components/Card';
import { Skeleton } from '../../components/StatusViews';
import type { AsyncResult } from '../../hooks/useAsync';
import type { PlayerMoves } from './PlayersMoves';
import { ordinal, ReplayPlayerCell } from './shared';

interface ClaimListProps {
  gameId: number;
  data: ReplayClaims;
  moves: PlayerMoves;
  openPlayer: (playerId: number) => void;
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
        const error = [moveKey(claim, -1), moveKey(claim, 1), cancelKey(claim)].map(moves.errorFor).find(Boolean);
        return (
          <li key={claim.id} className="replay-claims__item" data-claim={claim.id}>
            <span className="replay-claims__move">
              <ReplayPlayerCell player={claim.add.player} info={claim.add} onOpen={openPlayer} />
              <span className="hint">{claim.drop ? `Drop ${claim.drop.player.name}` : 'No drop'}</span>
            </span>
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

interface ClaimsPanelProps {
  gameId: number;
  claims: AsyncResult<ReplayClaims>;
  moves: PlayerMoves;
  openPlayer: (playerId: number) => void;
}

/** The waiver order and the user's pending claims, in the order they will be tried. */
export function ClaimsPanel({ gameId, claims, moves, openPlayer }: ClaimsPanelProps) {
  return (
    <Card title="Waiver claims" className="replay-claims">
      <AsyncView state={claims} loading={<Skeleton lines={3} label="Loading waiver claims…" />} errorTitle="Couldn't load your waiver claims">
        {(data) => (
          <>
            <p className="replay-claims__rule">
              {data.priority !== null && <><strong>You are {ordinal(data.priority)} of {data.waiver_order.length}</strong> in the waiver order. </>}
              On “Sim to Wednesday” claims are awarded in this order: a team that wins a player moves to the back of the line, and
              players nobody claims become free agents.
            </p>
            <ol className="replay-claims__order" aria-label="Waiver order">
              {data.waiver_order.map((team) => (
                <li key={team.team_id} className={team.is_user ? 'replay-claims__team replay-claims__team--mine' : 'replay-claims__team'}>
                  {team.name}{team.is_user && ' (you)'}
                </li>
              ))}
            </ol>
            {data.results.length === 0
              ? <p className="hint">You have no claims in. Press Claim next to a player below.</p>
              : (
                <>
                  <h3 className="section-title">Your claims, tried from the top</h3>
                  <ClaimList gameId={gameId} data={data} moves={moves} openPlayer={openPlayer} />
                </>
              )}
          </>
        )}
      </AsyncView>
    </Card>
  );
}
