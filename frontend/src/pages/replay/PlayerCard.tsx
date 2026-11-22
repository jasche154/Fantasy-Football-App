import type { ReplayPlayerDetail, ReplayPlayerWeek, ReplayPosition, ReplayWireRow } from '../../api/types';
import { Tabs } from '../../components/Tabs';
import { formatNumber } from '../../lib/format';
import { ADVANCED_POSITIONS, PlayerAdvanced } from './PlayerAdvanced';
import { PlayerGameLog } from './PlayerGameLog';
import { PlayerUpcoming } from './PlayerUpcoming';
import { formatPoints, injuryLabel, ReplayInjuryBadge, ReplayPositionChip, showsOut } from './shared';

function statusText(player: ReplayWireRow): string {
  if (player.owner) return player.owner.is_user ? 'On your team' : `Rostered by ${player.owner.name}`;
  return player.status === 'waivers' ? 'On waivers' : 'Free agent';
}

function Header({ row }: { row: ReplayWireRow }) {
  const { injury } = row.player;
  return (
    <header className="replay-card__head">
      <p className="replay-drawer__status">
        <ReplayPositionChip position={row.player.position} /> {row.player.team ?? 'No team'} · {row.pos_rank} · {statusText(row)}
        {injury && <> <ReplayInjuryBadge injury={injury} /></>}
        {showsOut(row.out, injury) && <span className="status-badge status-badge--out replay-drawer__badge">Out</span>}
        {row.trade_locked && <span className="status-badge status-badge--missing replay-drawer__badge">Trade-locked</span>}
      </p>
      {/* Hover isn't there on touch or for the keyboard: the full label in plain text. */}
      {injury && <p className="replay-card__injury">{injuryLabel(injury)}</p>}
      {row.news && <p className="replay-card__news">{row.news.text}</p>}
    </header>
  );
}

function Tiles({ row }: { row: ReplayWireRow }) {
  const tiles = [
    { label: 'Games', value: String(row.games) },
    { label: 'Points', value: formatPoints(row.season_points) },
    { label: 'Per game', value: formatPoints(row.ppg) },
    { label: 'Rest of season', value: formatPoints(row.ros_ppg) },
    { label: 'Trade value', value: formatNumber(row.value) },
  ];
  return (
    <dl className="replay-card__tiles">
      {tiles.map(({ label, value }) => <div key={label} className="replay-card__tile"><dt>{label}</dt><dd>{value}</dd></div>)}
    </dl>
  );
}

/** Game log, plus an Advanced tab (usage and efficiency) for skill players; K and DST get the game log alone. */
function WeeksTabs({ name, position, weeks }: { name: string; position: ReplayPosition; weeks: readonly ReplayPlayerWeek[] }) {
  if (!ADVANCED_POSITIONS.includes(position)) return <PlayerGameLog name={name} position={position} weeks={weeks} />;
  const log = <PlayerGameLog name={name} position={position} weeks={weeks} hasHeading={false} />;
  return (
    <Tabs label={`${name} stats`} className="replay-adv__tabs" tabs={[
      { id: 'log', label: 'Game log', panel: log },
      { id: 'advanced', label: 'Advanced', panel: <PlayerAdvanced name={name} position={position} weeks={weeks} /> },
    ]} />
  );
}

/** The player card: who he is and his news, five season numbers, his game log (and advanced stats) and the weeks ahead. */
export function PlayerCard({ detail }: { detail: ReplayPlayerDetail }) {
  const { player: row, weeks, upcoming, defense_basis: basis } = detail;
  const { name, position } = row.player;
  return (
    <div className="replay-card">
      <Header row={row} />
      <Tiles row={row} />
      {/* During the draft there are no weeks yet. */}
      {weeks.length === 0
        ? <p className="hint">The season has not started: these are his preseason numbers.</p>
        : <WeeksTabs name={name} position={position} weeks={weeks} />}
      <PlayerUpcoming name={name} position={position} upcoming={upcoming} basis={basis} />
    </div>
  );
}
