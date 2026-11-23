import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { advanceReplay } from '../../api/replay';
import type { ReplayEvent, ReplaySummary } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { Icon } from '../../components/Icon';
import { ErrorState, Skeleton } from '../../components/StatusViews';
import { useDocumentTitle } from '../../hooks/useDocumentTitle';
import { pluralize } from '../../lib/format';
import { buildHash, paths } from '../../router/routes';
import { DraftView } from './DraftView';
import { EventsPanel } from './EventsPanel';
import { LeagueView } from './LeagueView';
import { MatchupView } from './MatchupView';
import { PlayerDrawer } from './PlayerDrawer';
import { PlayersView } from './PlayersView';
import { TeamView } from './TeamView';
import { TradesView } from './TradesView';
import { GameNav } from './GameNav';
import { Scorebug } from './Scorebug';
import { groupEvents, ordinal, REPLAY_VIEWS, type ReplayView, type ReplayViewProps } from './shared';
import { useGame } from './useGame';

const VIEWS: Record<ReplayView, (props: ReplayViewProps) => ReactNode> = {
  team: TeamView,
  matchup: MatchupView,
  players: PlayersView,
  trades: TradesView,
  league: LeagueView,
};

/** One line for screen readers after an action: the user's own news first, then how much more is in the panel. */
function announce(game: ReplaySummary, events: readonly ReplayEvent[]): string {
  if (events.length === 0) return '';
  const lead = events.find((item) => item.team?.is_user) ?? groupEvents(events)[0]?.events[0] ?? events[0];
  const draftDone = game.phase !== 'draft' && events.some((item) => item.kind === 'draft') ? 'The draft is complete. ' : '';
  const more = events.length > 1 ? ` ${pluralize(events.length - 1, 'more update')} under “What just happened”.` : '';
  return `${draftDone}${lead.text}.${more}`;
}

function ChampionBanner({ game }: { game: ReplaySummary }) {
  const { champion, record } = game;
  if (game.phase !== 'complete' || !champion) return null;
  return (
    <p className="replay-champion" role="status">
      <Icon name="trophy" />
      <span>
        <strong>{champion.name}</strong> won the {game.season} championship.{' '}
        {champion.is_user ? 'That is your team: congratulations!' : record.place !== null && `You finished ${ordinal(record.place)}.`}
      </span>
    </p>
  );
}

interface GameHubProps {
  gameId: number;
  view: ReplayView;
}

/** The game: the scorebug (with the one primary button) and section tabs, what just happened, and the current screen. */
export function GameHub({ gameId, view }: GameHubProps) {
  const { loaded, game, version, act, isBusy, actError, clearError, events, dismissEvents } = useGame(gameId);
  useEffect(clearError, [view, clearError]);       // a refusal on one tab is not news on the next
  const [playerId, setPlayerId] = useState<number | null>(null);
  const [isAdvancing, setIsAdvancing] = useState(false);
  const viewRef = useRef<HTMLElement>(null);
  const closePlayer = useCallback(() => setPlayerId(null), []);
  useDocumentTitle(game?.name ?? 'Replay League');
  // The draft room unmounts when the season starts, taking focus with it: hand focus to the new screen.
  const phase = game?.phase;
  const previousPhase = useRef(phase);
  useEffect(() => {
    if (previousPhase.current === 'draft' && phase !== 'draft') viewRef.current?.focus();
    previousPhase.current = phase;
  }, [phase]);

  if (!game) {
    return (
      <>
        <header className="game-top"><Scorebug game={null} /></header>
        <main id="main-content" className="game-body game-main" tabIndex={-1}>
          <AsyncView state={loaded} loading={<Skeleton lines={5} label="Loading your league…" />}
            renderError={(error, retry) => (
              <>
                <ErrorState error={error} title="Couldn't open this league" onRetry={retry} />
                <p><a href={buildHash(paths.replay)}>Back to your leagues</a></p>
              </>
            )}>
            {() => null}
          </AsyncView>
        </main>
      </>
    );
  }

  const { step, label } = game.next_action;
  const isDraft = game.phase === 'draft';
  const press = async () => {
    if (step !== 'wednesday' && step !== 'play') return viewRef.current?.focus();       // "pick": go to the draft
    setIsAdvancing(true);
    await act(() => advanceReplay(game.id, step, game.week));
    setIsAdvancing(false);
  };
  const pendingLabel = step === 'wednesday' ? 'Running waivers…' : `Playing week ${game.week}…`;
  const View = isDraft ? DraftView : VIEWS[view];
  const viewLabel = isDraft ? 'Draft' : (REPLAY_VIEWS.find((tab) => tab.value === view)?.label ?? '');

  return (
    <>
      <header className="game-top">
        <Scorebug game={game} advanceLabel={isAdvancing ? pendingLabel : label} isAdvanceDisabled={isBusy || step === 'done'} onAdvance={press} />
        {!isDraft && <GameNav game={game} view={view} />}
      </header>
      <main id="main-content" className="game-body game-main" tabIndex={-1}>
        <ChampionBanner game={game} />
        {actError && <p className="error-text replay-alert" role="alert">{actError}</p>}
        <p className="sr-only" role="status">{announce(game, events)}</p>
        {events.length > 0 && <EventsPanel key={events[0].id} events={events} onDismiss={dismissEvents} />}
        <section ref={viewRef} key={isDraft ? 'draft' : view} className="game-view" tabIndex={-1} aria-label={viewLabel} data-game-view>
          <View game={game} version={version} act={act} isBusy={isBusy} actError={actError} openPlayer={setPlayerId} />
        </section>
      </main>
      {playerId !== null && <PlayerDrawer key={playerId} gameId={game.id} playerId={playerId} version={version} simFrom={game.sim_from} onClose={closePlayer} />}
    </>
  );
}
