import { parsePositiveInt } from '../../router/routes';
import { GameHub } from './GameHub';
import { ReplayHome } from './ReplayHome';
import { REPLAY_VIEWS, type ReplayView } from './shared';

function toView(value: string | undefined): ReplayView {
  return REPLAY_VIEWS.find((tab) => tab.value === value)?.value ?? 'team';
}

/** #/replay (saved leagues + new league) and #/replay?game=ID&view=team|matchup|players|trades|league (the hub). */
export function ReplayPage({ query }: { query: Readonly<Record<string, string>> }) {
  const gameId = parsePositiveInt(query.game);
  return gameId === null ? <ReplayHome /> : <GameHub key={gameId} gameId={gameId} view={toView(query.view)} />;
}
