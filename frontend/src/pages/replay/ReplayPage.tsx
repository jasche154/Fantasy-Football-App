import { useEffect } from 'react';
import { parsePositiveInt, paths } from '../../router/routes';
import { navigate } from '../../router/useHashLocation';
import { GameHub } from './GameHub';
import { ReplayHome } from './ReplayHome';
import { REPLAY_VIEWS, type ReplayView } from './shared';

type Query = Readonly<Record<string, string>>;

export function toView(value: string | undefined): ReplayView {
  return REPLAY_VIEWS.find((tab) => tab.value === value)?.value ?? 'team';
}

/** #/replay: the saved leagues and the new-league form. An old #/replay?game=ID... link moves on to #/play with its query. */
export function ReplayPage({ query }: { query: Query }) {
  const hasGame = query.game !== undefined && parsePositiveInt(query.game) !== null;
  useEffect(() => {
    if (hasGame) navigate(paths.play, query, { replace: true });
  }, [hasGame, query]);
  return hasGame ? null : <ReplayHome />;
}

/** #/play?game=ID&view=team|matchup|players|trades|league[&week=W&team=T]: the game screen (week and team open that
 *  game on the Matchup tab). A bad id goes back to the lobby. */
export function PlayPage({ query }: { query: Query }) {
  const gameId = parsePositiveInt(query.game);
  useEffect(() => {
    if (gameId === null) navigate(paths.replay, {}, { replace: true });
  }, [gameId]);
  const week = parsePositiveInt(query.week);
  const teamId = parsePositiveInt(query.team);
  const matchupAt = week !== null && teamId !== null ? { week, teamId } : undefined;
  return gameId === null ? null : <GameHub key={gameId} gameId={gameId} view={toView(query.view)} matchupAt={matchupAt} />;
}
