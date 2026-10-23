import type { PlayerSummary } from '../api/types';
import { isPosition } from '../lib/positions';
import { buildHash, paths } from '../router/routes';

interface PositionChipProps {
  position: string | null;
  size?: 'sm' | 'md';
}

/** Position badge. The text label carries the meaning; colour is reinforcement only. */
export function PositionChip({ position, size = 'sm' }: PositionChipProps) {
  if (!position) return null;
  const tone = isPosition(position) ? position.toLowerCase() : 'other';
  return <span className={`pos-chip pos-chip--${tone} pos-chip--${size}`}>{position}</span>;
}

export function playerHref(id: number): string {
  return buildHash(paths.player(id));
}

interface PlayerLinkProps {
  player: PlayerSummary;
  /** Show the position chip and team next to the name. */
  showMeta?: boolean;
}

export function PlayerLink({ player, showMeta = true }: PlayerLinkProps) {
  return (
    <span className="player-cell">
      <a className="player-link" href={playerHref(player.id)}>
        {player.name}
      </a>
      {showMeta && (
        <span className="player-cell__meta">
          <PositionChip position={player.position} />
          {player.team && <span className="player-cell__team">{player.team}</span>}
        </span>
      )}
    </span>
  );
}
