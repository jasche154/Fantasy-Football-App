import { getReplayPlayer } from '../../api/replay';
import { AsyncView } from '../../components/AsyncView';
import { Skeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { PlayerCard } from './PlayerCard';
import { ReplayDrawer } from './shared';

interface PlayerDrawerProps {
  gameId: number;
  playerId: number;
  /** The hub's version, so an open drawer follows the game. */
  version: number;
  onClose: () => void;
}

/** The hub's player card, in a drawer (a sheet on a phone). */
export function PlayerDrawer({ gameId, playerId, version, onClose }: PlayerDrawerProps) {
  const detail = useAsync((signal) => getReplayPlayer(gameId, playerId, signal), [gameId, playerId, version]);
  const name = detail.data?.player.player.name ?? 'Player';
  return (
    <ReplayDrawer eyebrow="Player" title={name} label={`${name} details`} closeLabel="Close player details" size="wide" onClose={onClose}>
      <AsyncView state={detail} loading={<Skeleton lines={6} label="Loading player…" />} errorTitle="Couldn't load this player">
        {(data) => <PlayerCard detail={data} />}
      </AsyncView>
    </ReplayDrawer>
  );
}
