import type { ReplayPlayerRow, ReplayTeam } from '../../api/types';
import { EMPTY_VALUE, formatNumber } from '../../lib/format';
import { canGoOnIr, ConfirmButton, formatPoints, ReplayPlayerCell } from './shared';
import { canSwap, type LineupSpot } from './teamLineup';

/** Lineup editing, passed only when the user may change this roster. */
export interface RosterEditing {
  /** The spot whose player is being moved, if any. */
  source: LineupSpot | null;
  /** The action in flight: "move:<spot key>", "drop:<id>", "ir:<id>" or "activate:<id>". */
  pending: string | null;
  isDisabled: boolean;
  onMove: (spot: LineupSpot) => void;
  onDrop: (playerId: number) => void;
  /** Moves an active player to IR (swapping him with the player there, if any). */
  onIr: (playerId: number) => void;
  onActivate: (playerId: number) => void;
}

interface TeamRosterTableProps {
  team: ReplayTeam;
  spots: readonly LineupSpot[];
  openPlayer: (playerId: number) => void;
  editing?: RosterEditing;
  /** Show the IR slot (the current, unplayed week only). */
  showIr?: boolean;
}

/** The row's lineup button: the Move toggle (`pressed` set), or a place to put the player being moved. */
function moveButton(spot: LineupSpot, source: LineupSpot | null): { text: string; label: string; pressed?: boolean } | null {
  const name = spot.row?.player.name;
  const moving = source?.row?.player.name;
  if (!source) return name ? { text: 'Move', label: `Move ${name}`, pressed: false } : null;
  if (spot.key === source.key) return { text: 'Cancel', label: `Cancel moving ${moving}`, pressed: true };
  if (!canSwap(source, spot)) return null;
  return name ? { text: 'Swap', label: `Swap ${moving} with ${name}` } : { text: 'Move here', label: `Move ${moving} to the empty ${spot.slot} slot` };
}

interface IrButtonProps {
  name: string;
  playerId: number;
  /** The player now on IR, if any: then it is a swap. */
  irName: string | undefined;
  editing: RosterEditing;
}

/** "Move to IR" for an active player tagged O, IR or PUP; "Swap to IR" when the slot is taken. */
function IrButton({ name, playerId, irName, editing }: IrButtonProps) {
  const isPending = editing.pending === `ir:${playerId}`;
  const [text, busy] = irName ? ['Swap to IR', 'Swapping…'] : ['Move to IR', 'Moving…'];
  // The name starts with the visible text (WCAG 2.5.3), so speech input and the pending text both work.
  return (
    <button type="button" className="btn btn--ghost btn--sm" disabled={editing.isDisabled} onClick={() => editing.onIr(playerId)}>
      {isPending ? busy : text}{' '}<span className="sr-only">{name}{irName ? `, activating ${irName}` : ''}</span>
    </button>
  );
}

function DropButton({ playerId, name, editing }: { playerId: number; name: string; editing: RosterEditing }) {
  return (
    <ConfirmButton label={editing.pending === `drop:${playerId}` ? 'Dropping…' : 'Drop'} confirmLabel="Yes, drop"
      subject={name} disabled={editing.isDisabled} onConfirm={() => editing.onDrop(playerId)} />
  );
}

function Actions({ spot, editing, irName }: { spot: LineupSpot; editing: RosterEditing; irName: string | undefined }) {
  const { source, pending, isDisabled, onMove } = editing;
  const move = moveButton(spot, source);
  const player = spot.row?.player;
  return (
    <td className="replay-roster__actions">
      {move && (
        <button type="button" className="btn btn--ghost btn--sm" aria-label={move.label} aria-pressed={move.pressed}
          disabled={isDisabled} onClick={() => onMove(spot)}>
          {pending === `move:${spot.key}` ? 'Saving…' : move.text}
        </button>
      )}
      {player && !source && canGoOnIr(player.injury) && <IrButton name={player.name} playerId={player.id} irName={irName} editing={editing} />}
      {player && !source && <DropButton key={player.id} playerId={player.id} name={player.name} editing={editing} />}
    </td>
  );
}

/** The seven columns after Slot, the same for every row. */
function PlayerCells({ row, openPlayer }: { row: ReplayPlayerRow; openPlayer: (playerId: number) => void }) {
  return (
    <>
      {/* The Opp column carries the opponent and the bye, so the cell only adds the tag, Out and Trade-locked. */}
      <td className="td--player"><ReplayPlayerCell player={row.player} info={{ out: row.out, trade_locked: row.trade_locked }} onOpen={openPlayer} /></td>
      <td>{row.opponent ?? EMPTY_VALUE}</td>
      <td className="num">{formatPoints(row.projection)}</td>
      <td className="num">{formatPoints(row.points)}</td>
      <td className="num">{row.games > 0 ? formatPoints(row.ppg) : EMPTY_VALUE}</td>
      <td className="num">{formatPoints(row.ros_ppg)}</td>
      <td className="num">{formatNumber(row.value)}</td>
    </>
  );
}

function rowCells(row: ReplayPlayerRow | null | undefined, openPlayer: (playerId: number) => void) {
  return row ? <PlayerCells row={row} openPlayer={openPlayer} /> : <td className="muted" colSpan={7}>Empty</td>;
}

interface SpotRowProps {
  spot: LineupSpot;
  openPlayer: (playerId: number) => void;
  editing?: RosterEditing;
  irName: string | undefined;
}

function SpotRow({ spot, openPlayer, editing, irName }: SpotRowProps) {
  const { row } = spot;
  const source = editing?.source ?? null;
  const isTarget = source !== null && canSwap(source, spot);
  const className = [spot.key === source?.key ? 'replay-roster__row--moving' : '', isTarget ? 'replay-roster__row--target' : '',
    row?.starting && (row.bye || row.out || row.player.injury) ? 'replay-roster__row--warn' : ''].filter(Boolean).join(' ');
  return (
    <tr className={className || undefined}>
      <td className="td--pos">{spot.slot}</td>
      {rowCells(row, openPlayer)}
      {editing && <Actions spot={spot} editing={editing} irName={irName} />}
    </tr>
  );
}

/** The IR slot: its player (Activate, Drop; no lineup move, he can't start) or Empty. */
function IrRow({ row, openPlayer, editing }: { row: ReplayPlayerRow | undefined; openPlayer: (playerId: number) => void; editing?: RosterEditing }) {
  const player = row?.player;
  return (
    <tr>
      <td className="td--pos">IR</td>
      {rowCells(row, openPlayer)}
      {editing && (
        <td className="replay-roster__actions">
          {player && !editing.source && (
            <>
              <button type="button" className="btn btn--ghost btn--sm" disabled={editing.isDisabled} onClick={() => editing.onActivate(player.id)}>
                {editing.pending === `activate:${player.id}` ? 'Activating…' : 'Activate'}{' '}<span className="sr-only">{player.name}</span>
              </button>
              <DropButton key={player.id} playerId={player.id} name={player.name} editing={editing} />
            </>
          )}
        </td>
      )}
    </tr>
  );
}

/** Yahoo-style roster: starters in slot order with their total, then the bench, then the IR slot (current week). */
export function TeamRosterTable({ team, spots, openPlayer, editing, showIr = false }: TeamRosterTableProps) {
  const starters = spots.filter((spot) => spot.slot !== 'BN');
  const bench = spots.filter((spot) => spot.slot === 'BN');
  const irName = team.ir[0]?.player.name;
  const row = (spot: LineupSpot) => <SpotRow key={spot.key} spot={spot} openPlayer={openPlayer} editing={editing} irName={irName} />;
  return (
    <div className="table-wrap">
      <table className="data-table data-table--compact replay-roster">
        <caption className="sr-only">{team.team.name} roster, week {team.week}</caption>
        <thead>
          <tr>
            <th scope="col">Slot</th>
            <th scope="col">Player</th>
            <th scope="col">Opp</th>
            <th scope="col" className="num">Proj</th>
            <th scope="col" className="num">Pts</th>
            <th scope="col" className="num" title="Points per game played before this week">PPG</th>
            <th scope="col" className="num" title="Expected points a game from here on, if healthy">ROS</th>
            <th scope="col" className="num" title="What he counts for in a trade">Value</th>
            {editing && <th scope="col">Actions</th>}
          </tr>
        </thead>
        <tbody>
          {starters.map(row)}
          <tr className="replay-roster__total">
            <th scope="row" colSpan={3}>Starters total</th>
            <td className="num">{formatPoints(team.projected_total)}</td>
            <td className="num">{formatPoints(team.actual_total)}</td>
            <td colSpan={editing ? 4 : 3} />
          </tr>
        </tbody>
        {bench.length > 0 && <tbody>{bench.map(row)}</tbody>}
        {showIr && <tbody><IrRow row={team.ir[0]} openPlayer={openPlayer} editing={editing} /></tbody>}
      </table>
    </div>
  );
}
