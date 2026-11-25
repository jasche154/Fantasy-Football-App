import { useId } from 'react';
import type { ReplayPlayerDetail, ReplayPlayerWeek, ReplayPosition, ReplayWireRow } from '../../api/types';
import { Tabs } from '../../components/Tabs';
import { formatInteger } from '../../lib/format';
import { ADVANCED_POSITIONS, PlayerAdvanced } from './PlayerAdvanced';
import { PlayerGameLog } from './PlayerGameLog';
import { PlayerUpcoming } from './PlayerUpcoming';
import { formatPoints, hypeLabel, injuryLabel, ReplayHypeStar, ReplayInjuryBadge, ReplayOutBadge, ReplayPositionChip, showsOut } from './shared';

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
        {row.player.hype && <> <ReplayHypeStar player={row.player} /></>}
        {injury && <> <ReplayInjuryBadge injury={injury} /></>}
        {showsOut(row.out, injury) && <ReplayOutBadge className="replay-drawer__badge" />}
        {row.trade_locked && <span className="status-badge status-badge--missing replay-drawer__badge">Trade-locked</span>}
      </p>
      {/* Hover isn't there on touch or for the keyboard: the full label in plain text. */}
      {injury && <p className="replay-card__injury">{injuryLabel(injury)}</p>}
      {row.player.hype && <p className="replay-card__hype" aria-hidden="true">{hypeLabel(row.player)}</p>}
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
    { label: 'Trade value', value: formatInteger(row.value) },
  ];
  return (
    <dl className="replay-card__tiles">
      {tiles.map(({ label, value }) => <div key={label} className="replay-card__tile"><dt>{label}</dt><dd>{value}</dd></div>)}
    </dl>
  );
}

/** A simulated season's news lines about his role, newest first: rumors, worded as such (never an injury). */
function Buzz({ buzz }: { buzz: ReplayPlayerDetail['buzz'] }) {
  const headingId = useId();
  if (buzz.length === 0) return null;
  return (
    <section className="replay-card__section" aria-labelledby={headingId}>
      <h3 id={headingId} className="game-subhead replay-card__subhead">News</h3>
      <ul className="replay-card__buzz">
        {buzz.map((line) => (
          <li key={line.week}><span className="replay-card__buzz-week">Week {line.week}</span> {line.text}</li>
        ))}
      </ul>
      <p className="hint">Rumors from around the league, heard before that week's games. They are right only some of the time.</p>
    </section>
  );
}

interface WeeksTabsProps {
  name: string;
  position: ReplayPosition;
  weeks: readonly ReplayPlayerWeek[];
  simFrom: number | null;
  onOpenGame?: (week: number, team: string) => void;
}

/** Game log, plus an Advanced tab (usage and efficiency) for skill players; K and DST get the game log alone. */
function WeeksTabs({ name, position, weeks, simFrom, onOpenGame }: WeeksTabsProps) {
  if (!ADVANCED_POSITIONS.includes(position)) return <PlayerGameLog name={name} position={position} weeks={weeks} simFrom={simFrom} onOpenGame={onOpenGame} />;
  const log = <PlayerGameLog name={name} position={position} weeks={weeks} hasHeading={false} simFrom={simFrom} onOpenGame={onOpenGame} />;
  return (
    <Tabs label={`${name} stats`} className="replay-adv__tabs" tabs={[
      { id: 'log', label: 'Game log', panel: log },
      { id: 'advanced', label: 'Advanced', panel: <PlayerAdvanced name={name} position={position} weeks={weeks} /> },
    ]} />
  );
}

interface PlayerCardProps {
  detail: ReplayPlayerDetail;
  /** The league's first simulated week (Summary.sim_from): its weeks from there on are marked Sim. */
  simFrom?: number | null;
  /** Opens a played week's NFL box score from the game log. */
  onOpenGame?: (week: number, team: string) => void;
}

/** The player card: who he is and his news, five season numbers, rumors, his game log (and advanced stats) and the weeks ahead. */
export function PlayerCard({ detail, simFrom = null, onOpenGame }: PlayerCardProps) {
  const { player: row, weeks, upcoming, defense_basis: basis } = detail;
  const { name, position } = row.player;
  return (
    <div className="replay-card">
      <Header row={row} />
      <Tiles row={row} />
      <Buzz buzz={detail.buzz} />
      {/* During the draft there are no weeks yet. */}
      {weeks.length === 0
        ? <p className="hint">The season has not started: these are his preseason numbers.</p>
        : <WeeksTabs name={name} position={position} weeks={weeks} simFrom={simFrom} onOpenGame={onOpenGame} />}
      <PlayerUpcoming name={name} position={position} upcoming={upcoming} basis={basis} simFrom={simFrom} />
    </div>
  );
}
