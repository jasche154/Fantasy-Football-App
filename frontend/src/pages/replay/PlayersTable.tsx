import type { ReplaySummary, ReplayWireRow } from '../../api/types';
import { EMPTY_VALUE, formatNumber } from '../../lib/format';
import { buildHash, paths } from '../../router/routes';
import type { PlayerMoves } from './PlayersMoves';
import { formatPoints, irBlockedText, ReplayPlayerCell } from './shared';

/** The key of a direct "Add" (the one move a table row sends itself). */
export function addKey(playerId: number): string {
  return `add:${playerId}`;
}

function StatusCell({ row }: { row: ReplayWireRow }) {
  if (row.owner) return <>{row.owner.name}</>;
  return row.status === 'waivers' ? <abbr title="On waivers">W</abbr> : <abbr title="Free agent">FA</abbr>;
}

interface RowActionProps {
  game: ReplaySummary;
  row: ReplayWireRow;
  hasClaim: boolean;
  moves: PlayerMoves;
  onAdd: (row: ReplayWireRow) => void;
  onClaim: (row: ReplayWireRow) => void;
}

/**
 * What the user can do with a player right now (the backend's rules): a free agent is added while the week
 * is open; a player on waivers is claimed before the waiver run, and otherwise waits for next week; a CPU's
 * player goes to the Trades tab.
 */
function RowAction({ game, row, hasClaim, moves, onAdd, onClaim }: RowActionProps) {
  const { player, owner, status } = row;
  // The space sits outside the hidden span so every accessible-name algorithm keeps it.
  const name = <>{' '}<span className="sr-only">{player.name}</span></>;
  if (owner?.is_user) return <span className="muted">Your player</span>;
  if (game.phase !== 'open' && game.phase !== 'waivers') return null;
  if (owner) {
    if (game.week > game.settings.trade_deadline_week) return null;
    // The Trades tab reads `with` (team id) and `want` (player id) from the query.
    return <a href={buildHash(paths.replay, { game: game.id, view: 'trades', with: owner.team_id, want: player.id })}>Trade{' '}<span className="sr-only">for {player.name}</span></a>;
  }
  // The server refuses adds and claims while an IR player has lost his tag; the banner says why.
  const blocked = irBlockedText(game) ?? undefined;
  if (game.phase === 'waivers') {
    if (hasClaim) return <span className="muted">Claim in</span>;
    return <button type="button" className="btn btn--primary btn--sm" disabled={moves.isBusy || !!blocked} title={blocked} onClick={() => onClaim(row)}>Claim{name}</button>;
  }
  if (status !== 'free_agent') {
    return <span className="muted" title="Dropped this week, so he can't be added until next week">Next week</span>;
  }
  return (
    <button type="button" className="btn btn--primary btn--sm" disabled={moves.isBusy || !!blocked} title={blocked} onClick={() => onAdd(row)}>
      {moves.pendingKey === addKey(player.id) ? 'Adding…' : 'Add'}{name}
    </button>
  );
}

interface PlayersTableProps {
  game: ReplaySummary;
  week: number;
  rows: readonly ReplayWireRow[];
  /** Players the user already has a claim in for. */
  claimedIds: ReadonlySet<number>;
  moves: PlayerMoves;
  onAdd: (row: ReplayWireRow) => void;
  onClaim: (row: ReplayWireRow) => void;
  openPlayer: (playerId: number) => void;
}

/** The waiver wire. Every points column is from weeks already played; Proj is this week's pre-game projection. */
export function PlayersTable({ game, week, rows, claimedIds, moves, onAdd, onClaim, openPlayer }: PlayersTableProps) {
  return (
    <div className="table-wrap">
      <table className="data-table data-table--compact">
        <caption className="sr-only">Players, week {week}</caption>
        <thead>
          <tr>
            <th scope="col">Player</th>
            <th scope="col">Status</th>
            <th scope="col">Opp</th>
            <th scope="col" className="num th--end" title={`Week ${week} projection`}>Proj</th>
            <th scope="col" className="num th--end" title="Points in the last week played">Last</th>
            <th scope="col" className="num th--end" title="Points this season">Season</th>
            <th scope="col" className="num th--end" title="Points per game played">PPG</th>
            <th scope="col" className="num th--end" title="Expected points per game for the rest of the season">ROS</th>
            <th scope="col" className="num th--end" title="Trade value">Value</th>
            <th scope="col"><span className="sr-only">Action</span></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const error = moves.errorFor(addKey(row.player.id));
            return (
              <tr key={row.player.id} className={row.owner?.is_user ? 'row--mine' : undefined}>
                <td className="td--player">
                  <ReplayPlayerCell player={row.player} info={{ out: row.out, trade_locked: row.trade_locked }} onOpen={openPlayer} />
                </td>
                <td><StatusCell row={row} /></td>
                <td>{row.bye ? 'BYE' : (row.opponent ?? EMPTY_VALUE)}</td>
                <td className="num">{formatPoints(row.projection)}</td>
                <td className="num">{formatPoints(row.last_points)}</td>
                <td className="num">{formatPoints(row.season_points)}</td>
                <td className="num">{row.games > 0 ? formatPoints(row.ppg) : EMPTY_VALUE}</td>
                <td className="num">{formatPoints(row.ros_ppg)}</td>
                <td className="num">{formatNumber(row.value)}</td>
                <td className="replay-players__action">
                  <RowAction game={game} row={row} hasClaim={claimedIds.has(row.player.id)} moves={moves} onAdd={onAdd} onClaim={onClaim} />
                  {/* The hub's alert announces it; this copy sits where the user is looking. */}
                  {error && <p className="error-text replay-players__error">{error}</p>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
