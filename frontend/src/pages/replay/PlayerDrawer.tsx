import { getReplayPlayer } from '../../api/replay';
import type { ReplayPlayerDetail, ReplayPlayerWeek, ReplayWireRow } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { Skeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { EMPTY_VALUE, formatNumber } from '../../lib/format';
import { formatPoints, injuryLabel, ReplayDrawer, ReplayInjuryBadge, ReplayPositionChip, showsOut } from './shared';

function statusText(player: ReplayWireRow): string {
  if (player.owner) return player.owner.is_user ? 'On your team' : `Rostered by ${player.owner.name}`;
  return player.status === 'waivers' ? 'On waivers' : 'Free agent';
}

function WeekRow({ week }: { week: ReplayPlayerWeek }) {
  return (
    <tr>
      <th scope="row" className="num">{week.week}</th>
      <td>
        {week.bye ? 'BYE' : (week.opponent ?? EMPTY_VALUE)}
        {week.injury && <> <ReplayInjuryBadge injury={week.injury} /></>}
        {showsOut(week.out, week.injury) && <span className="status-badge status-badge--out replay-drawer__badge">Out</span>}
      </td>
      <td className="num">{formatPoints(week.projection)}</td>
      {/* A week that hasn't been played never shows a result. */}
      <td className="num">{week.played ? formatPoints(week.points) : EMPTY_VALUE}</td>
      <td className="num">{formatNumber(week.value)}</td>
    </tr>
  );
}

function Detail({ detail }: { detail: ReplayPlayerDetail }) {
  const { player: row, weeks } = detail;
  const { injury } = row.player;
  return (
    <>
      <p className="replay-drawer__status">
        <ReplayPositionChip position={row.player.position} /> {row.player.team ?? 'No team'} · {row.pos_rank} · {statusText(row)}
        {injury && <> <ReplayInjuryBadge injury={injury} /></>}
        {showsOut(row.out, injury) && <span className="status-badge status-badge--out replay-drawer__badge">Out</span>}
        {row.trade_locked && <span className="status-badge status-badge--missing replay-drawer__badge">Trade-locked</span>}
      </p>
      {/* Hover isn't there on touch or for the keyboard: the full label in plain text. */}
      {injury && <p className="replay-drawer__status">{injuryLabel(injury)}</p>}
      <dl className="replay-drawer__line">
        <div><dt>Games</dt><dd>{row.games}</dd></div>
        <div><dt>Points</dt><dd>{formatPoints(row.season_points)}</dd></div>
        <div><dt>Per game</dt><dd>{formatPoints(row.ppg)}</dd></div>
        <div><dt>Rest of season</dt><dd>{formatPoints(row.ros_ppg)}</dd></div>
        <div><dt>Trade value</dt><dd>{formatNumber(row.value)}</dd></div>
      </dl>
      {/* During the draft there are no weeks yet. */}
      {weeks.length === 0 ? <p className="hint">The season has not started: these are his preseason numbers.</p> : (
        <>
          <div className="table-wrap">
            <table className="data-table data-table--compact">
              <caption className="sr-only">{row.player.name} week by week</caption>
              <thead>
                <tr>
                  <th scope="col" className="num">Wk</th>
                  <th scope="col">Opp</th>
                  <th scope="col" className="num">Proj</th>
                  <th scope="col" className="num">Pts</th>
                  <th scope="col" className="num">Value</th>
                </tr>
              </thead>
              <tbody>{weeks.map((week) => <WeekRow key={week.week} week={week} />)}</tbody>
            </table>
          </div>
          <p className="hint">Proj is the pre-game projection. Pts shows played weeks only; a dash means he did not play.</p>
        </>
      )}
    </>
  );
}

interface PlayerDrawerProps {
  gameId: number;
  playerId: number;
  /** The hub's version, so an open drawer follows the game. */
  version: number;
  onClose: () => void;
}

/** The hub's right-hand player card. */
export function PlayerDrawer({ gameId, playerId, version, onClose }: PlayerDrawerProps) {
  const detail = useAsync((signal) => getReplayPlayer(gameId, playerId, signal), [gameId, playerId, version]);
  const name = detail.data?.player.player.name ?? 'Player';
  return (
    <ReplayDrawer eyebrow="Player" title={name} label={`${name} details`} closeLabel="Close player details" onClose={onClose}>
      <AsyncView state={detail} loading={<Skeleton lines={6} label="Loading player…" />} errorTitle="Couldn't load this player">
        {(data) => <Detail detail={data} />}
      </AsyncView>
    </ReplayDrawer>
  );
}
