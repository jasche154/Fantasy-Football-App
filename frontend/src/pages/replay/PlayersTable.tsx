import type { ReplayPlayerSort, ReplaySummary, ReplayWireRow } from '../../api/types';
import { SortableHeader, type SortState } from '../../components/SortableHeader';
import { EMPTY_VALUE, formatNumber } from '../../lib/format';
import { buildHash, paths } from '../../router/routes';
import { DefenseChip } from './gameUi';
import type { StatColumn } from './playerColumns';
import type { PlayerMoves } from './PlayersMoves';
import { irBlockedText, ReplayPlayerCell } from './shared';

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
    return <a href={buildHash(paths.play, { game: game.id, view: 'trades', with: owner.team_id, want: player.id })}>Trade{' '}<span className="sr-only">for {player.name}</span></a>;
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
  /** The stat columns for the chosen span and position (playerColumns.statColumns). */
  columns: readonly StatColumn[];
  sort: SortState<ReplayPlayerSort>;
  onSort: (key: ReplayPlayerSort) => void;
  /** Players the user already has a claim in for. */
  claimedIds: ReadonlySet<number>;
  moves: PlayerMoves;
  onAdd: (row: ReplayWireRow) => void;
  onClaim: (row: ReplayWireRow) => void;
  openPlayer: (playerId: number) => void;
}

/**
 * The waiver wire. Clicking a header sorts on the server (every page, not just this one). The stat columns
 * follow the Stats picker; Opp carries how that defense treats his position. Scrolls inside its wrapper on a phone.
 */
export function PlayersTable({ game, week, rows, columns, sort, onSort, claimedIds, moves, onAdd, onClaim, openPlayer }: PlayersTableProps) {
  return (
    <div className="table-wrap">
      <table className="data-table data-table--compact replay-players__table">
        <caption className="sr-only">Players, week {week}</caption>
        <thead>
          <tr>
            <SortableHeader columnKey="name" sort={sort} onSort={onSort} align="start">Player</SortableHeader>
            <th scope="col">Status</th>
            <th scope="col" title={`Week ${week} opponent and how that defense treats his position`}>Opp</th>
            {columns.map((column) => (column.sort
              ? <SortableHeader key={column.id} columnKey={column.sort} sort={sort} onSort={onSort} title={column.title}>{column.label}</SortableHeader>
              : <th key={column.id} scope="col" className="num th--end"><abbr title={column.title}>{column.label}</abbr></th>))}
            <SortableHeader columnKey="value" sort={sort} onSort={onSort} title="Trade chart value, 0-1000">Value</SortableHeader>
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
                  {row.player.injury && row.news && <p className="hint">{row.news.text}</p>}
                </td>
                <td><StatusCell row={row} /></td>
                <td className="replay-players__opp">
                  {row.bye ? 'BYE' : (row.opponent ?? EMPTY_VALUE)}
                  {!row.bye && <DefenseChip rating={row.defense} position={row.player.position} />}
                </td>
                {columns.map((column) => <td key={column.id} className="num">{column.text(row)}</td>)}
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
