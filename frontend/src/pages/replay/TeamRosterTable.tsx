import type { ReplayTeam } from '../../api/types';
import { EMPTY_VALUE, formatNumber } from '../../lib/format';
import { ConfirmButton, formatPoints, ReplayPlayerCell } from './shared';
import { canSwap, type LineupSpot } from './teamLineup';

/** Lineup editing, passed only when the user may change this roster. */
export interface RosterEditing {
  /** The spot whose player is being moved, if any. */
  source: LineupSpot | null;
  /** The action in flight: "move:<spot key>" or "drop:<player id>". */
  pending: string | null;
  isDisabled: boolean;
  onMove: (spot: LineupSpot) => void;
  onDrop: (playerId: number) => void;
}

interface TeamRosterTableProps {
  team: ReplayTeam;
  spots: readonly LineupSpot[];
  openPlayer: (playerId: number) => void;
  editing?: RosterEditing;
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

function Actions({ spot, editing }: { spot: LineupSpot; editing: RosterEditing }) {
  const { source, pending, isDisabled, onMove, onDrop } = editing;
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
      {player && !source && (
        <ConfirmButton key={player.id} label={pending === `drop:${player.id}` ? 'Dropping…' : 'Drop'} confirmLabel="Yes, drop"
          subject={player.name} disabled={isDisabled} onConfirm={() => onDrop(player.id)} />
      )}
    </td>
  );
}

function SpotRow({ spot, openPlayer, editing }: { spot: LineupSpot; openPlayer: (playerId: number) => void; editing?: RosterEditing }) {
  const { row } = spot;
  const source = editing?.source ?? null;
  const isTarget = source !== null && canSwap(source, spot);
  const className = [spot.key === source?.key ? 'replay-roster__row--moving' : '', isTarget ? 'replay-roster__row--target' : '',
    row?.starting && (row.bye || row.out) ? 'replay-roster__row--warn' : ''].filter(Boolean).join(' ');
  return (
    <tr className={className || undefined}>
      <td className="td--pos">{spot.slot}</td>
      {row ? (
        <>
          {/* The Opp column carries the opponent and the bye, so the cell only adds Out and Trade-locked. */}
          <td className="td--player"><ReplayPlayerCell player={row.player} info={{ out: row.out, trade_locked: row.trade_locked }} onOpen={openPlayer} /></td>
          <td>{row.opponent ?? EMPTY_VALUE}</td>
          <td className="num">{formatPoints(row.projection)}</td>
          <td className="num">{formatPoints(row.points)}</td>
          <td className="num">{row.games > 0 ? formatPoints(row.ppg) : EMPTY_VALUE}</td>
          <td className="num">{formatPoints(row.ros_ppg)}</td>
          <td className="num">{formatNumber(row.value)}</td>
        </>
      ) : (
        <td className="muted" colSpan={7}>Empty</td>
      )}
      {editing && <Actions spot={spot} editing={editing} />}
    </tr>
  );
}

/** Yahoo-style roster: starters in slot order with their total, then the bench. */
export function TeamRosterTable({ team, spots, openPlayer, editing }: TeamRosterTableProps) {
  const starters = spots.filter((spot) => spot.slot !== 'BN');
  const bench = spots.filter((spot) => spot.slot === 'BN');
  const row = (spot: LineupSpot) => <SpotRow key={spot.key} spot={spot} openPlayer={openPlayer} editing={editing} />;
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
      </table>
    </div>
  );
}
