import { useRef, useState } from 'react';
import { getReplayPlayer } from '../../api/replay';
import { AsyncView } from '../../components/AsyncView';
import { Skeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { BoxScoreView } from './BoxScoreView';
import { PlayerCard } from './PlayerCard';
import { ReplayDrawer } from './shared';

interface PlayerDrawerProps {
  gameId: number;
  playerId: number;
  /** The hub's version, so an open drawer follows the game. */
  version: number;
  /** The league's first simulated week (Summary.sim_from), for the card's Sim markers. */
  simFrom?: number | null;
  onClose: () => void;
}

/** The hub's player card, in a drawer (a sheet on a phone); a game in his log opens that game's box score here. */
export function PlayerDrawer({ gameId, playerId, version, simFrom = null, onClose }: PlayerDrawerProps) {
  const detail = useAsync((signal) => getReplayPlayer(gameId, playerId, signal), [gameId, playerId, version]);
  const [game, setGame] = useState<{ week: number; team: string } | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const name = detail.data?.player.player.name ?? 'Player';
  const openGame = (week: number, team: string) => {
    opener.current = document.activeElement as HTMLElement | null;
    setGame({ week, team });
  };
  const back = () => {
    setGame(null);
    requestAnimationFrame(() => opener.current?.focus());     // the card stays mounted (hidden), so its button is there
  };
  return (
    <ReplayDrawer eyebrow="Player" title={name} label={`${name} details`} closeLabel="Close player details" size="wide" onClose={onClose}>
      {game && <BoxScoreView gameId={gameId} week={game.week} team={game.team} backLabel={`Back to ${name}`} onBack={back} />}
      <div hidden={game !== null}>
        <AsyncView state={detail} loading={<Skeleton lines={6} label="Loading player…" />} errorTitle="Couldn't load this player">
          {(data) => <PlayerCard detail={data} simFrom={simFrom} onOpenGame={openGame} />}
        </AsyncView>
      </div>
    </ReplayDrawer>
  );
}
