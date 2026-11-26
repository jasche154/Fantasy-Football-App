import { Fragment, useEffect, useRef, useState } from 'react';
import type { ReplayPlayerSort, ReplaySummary, ReplayWireRow } from '../../api/types';
import { SortableHeader, type SortState } from '../../components/SortableHeader';
import { EMPTY_VALUE, formatInteger } from '../../lib/format';
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
  if (row.status !== 'waivers') return <abbr title="Free agent">FA</abbr>;
  return <abbr title={row.after_games ? 'On waivers: claimable after this week’s games' : 'On waivers'}>W</abbr>;
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
    if (!game.trades_open) return null;
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

type Overflow = { start: boolean; end: boolean };

/** Which sides of a scroller hide columns, so the pinned columns cast a shadow only when something is under them. */
function useOverflow() {
  const ref = useRef<HTMLDivElement>(null);
  const [overflow, setOverflow] = useState<Overflow>({ start: false, end: false });
  useEffect(() => {
    const box = ref.current;
    if (!box) return undefined;
    const measure = () => {
      const start = box.scrollLeft > 1;
      const end = box.scrollLeft + box.clientWidth < box.scrollWidth - 1;
      setOverflow((now) => (now.start === start && now.end === end ? now : { start, end }));
    };
    measure();
    box.addEventListener('scroll', measure, { passive: true });
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(box);
    // The table changes width on its own (Box score / Advanced, another page) while the box keeps its size.
    const table = box.querySelector('table');
    if (table) observer?.observe(table);
    return () => {
      box.removeEventListener('scroll', measure);
      observer?.disconnect();
    };
  }, []);
  return { ref, overflow };
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
 * follow the Stats picker and view; Opp carries how that defense treats his position. Player stays pinned on the
 * left and Value + the row's action on the right, so only the stats scroll sideways when they don't fit.
 */
export function PlayersTable({ game, week, rows, columns, sort, onSort, claimedIds, moves, onAdd, onClaim, openPlayer }: PlayersTableProps) {
  const { ref, overflow } = useOverflow();
  return (
    <div ref={ref} className="table-wrap replay-players__wrap" data-overflow-start={overflow.start || undefined} data-overflow-end={overflow.end || undefined}>
      <table className="data-table data-table--compact replay-players__table">
        <caption className="sr-only">Players, week {week}</caption>
        <thead>
          <tr>
            <SortableHeader columnKey="name" sort={sort} onSort={onSort} align="start" className="replay-players__pin-start">Player</SortableHeader>
            <th scope="col">Status</th>
            <th scope="col" title={`Week ${week} opponent and how that defense treats his position`}>Opp</th>
            {columns.map((column) => (column.sort
              ? <SortableHeader key={column.id} columnKey={column.sort} sort={sort} onSort={onSort} title={column.title}>
                  {column.label}<span className="sr-only">{`, ${column.title}`}</span>
                </SortableHeader>
              : <th key={column.id} scope="col" className="num th--end"><abbr title={column.title}>{column.label}</abbr><span className="sr-only">{`, ${column.title}`}</span></th>))}
            <SortableHeader columnKey="value" sort={sort} onSort={onSort} title="Trade chart value, 0-1000" className="replay-players__pin-value">Value</SortableHeader>
            <th scope="col" className="replay-players__pin-action"><span className="sr-only">Action</span></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const error = moves.errorFor(addKey(row.player.id));
            return (
              <Fragment key={row.player.id}>
                <tr className={row.owner?.is_user ? 'row--mine' : undefined}>
                  <td className="td--player replay-players__pin-start">
                    <ReplayPlayerCell player={row.player} info={{ out: row.out, trade_locked: row.trade_locked, buzz: row.buzz }} onOpen={openPlayer} />
                    {row.news && <p className="hint">{row.news.text}</p>}
                  </td>
                  <td><StatusCell row={row} /></td>
                  <td className="replay-players__opp">
                    {row.bye ? 'BYE' : (row.opponent ?? EMPTY_VALUE)}
                    {!row.bye && <DefenseChip rating={row.defense} position={row.player.position} />}
                  </td>
                  {columns.map((column) => <td key={column.id} className="num">{column.text(row)}</td>)}
                  <td className="num replay-players__pin-value">{formatInteger(row.value)}</td>
                  <td className="replay-players__action replay-players__pin-action">
                    <RowAction game={game} row={row} hasClaim={claimedIds.has(row.player.id)} moves={moves} onAdd={onAdd} onClaim={onClaim} />
                  </td>
                </tr>
                {/* The hub's alert announces it; this copy sits under the row, where the user is looking, full width. */}
                {error && (
                  <tr className="replay-players__error-row">
                    <td colSpan={columns.length + 5}><p className="error-text replay-players__error">{error}</p></td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
