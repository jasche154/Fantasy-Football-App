import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { advanceReplay } from '../../api/replay';
import type { ReplayEvent, ReplaySummary } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { PageHeader } from '../../components/Card';
import { Icon } from '../../components/Icon';
import { ErrorState, Skeleton } from '../../components/StatusViews';
import { useDocumentTitle } from '../../hooks/useDocumentTitle';
import { pluralize } from '../../lib/format';
import { buildHash, paths } from '../../router/routes';
import { scoringLabel } from '../../state/leagueSettings';
import { DraftView } from './DraftView';
import { EventsPanel } from './EventsPanel';
import { LeagueView } from './LeagueView';
import { MatchupView } from './MatchupView';
import { PlayerDrawer } from './PlayerDrawer';
import { PlayersView } from './PlayersView';
import { TeamView } from './TeamView';
import { TradesView } from './TradesView';
import { formatPoints, formatRecord, goToView, groupEvents, ordinal, REPLAY_VIEWS, type ReplayView, type ReplayViewProps } from './shared';
import { useGame } from './useGame';

const VIEWS: Record<ReplayView, (props: ReplayViewProps) => ReactNode> = {
  team: TeamView,
  matchup: MatchupView,
  players: PlayersView,
  trades: TradesView,
  league: LeagueView,
};

function weekText(game: ReplaySummary): string {
  if (game.phase === 'draft') return 'Draft';
  return game.phase === 'complete' ? 'Season over' : `Week ${game.week}`;
}

/** The user's record and rank, and this week's game with both projections (the score once it is final). */
function Scoreline({ game }: { game: ReplaySummary }) {
  const { matchup, record } = game;
  if (game.phase === 'draft') return null;
  const mine = game.teams.find((team) => team.team_id === game.user_team_id);
  const isFinal = matchup?.you.points != null && matchup.opponent.points != null;
  return (
    <p className="replay-scoreline">
      <span><strong>{mine?.name}</strong> {formatRecord(record)} · {ordinal(record.rank)} of {game.settings.teams}</span>
      {matchup ? (
        <span>
          {matchup.label ?? `Week ${matchup.week}`} vs <strong>{matchup.opponent.name}</strong> ({formatRecord(matchup.opponent.record)}) ·{' '}
          {isFinal
            ? `final ${formatPoints(matchup.you.points)} – ${formatPoints(matchup.opponent.points)}`
            : `projected ${formatPoints(matchup.you.projected)} – ${formatPoints(matchup.opponent.projected)}`}
        </span>
      ) : (
        <span>No game this week</span>
      )}
    </p>
  );
}

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

function Tabs({ game, view }: { game: ReplaySummary; view: ReplayView }) {
  const badges: Partial<Record<ReplayView, { count: number; noun: string }>> = {
    players: { count: game.pending.claims, noun: 'pending claims' },
    trades: { count: game.pending.offers, noun: 'offers to answer' },
  };
  return (
    <nav className="replay-tabs" aria-label="League sections">
      <div className="segmented replay-segmented">
        {REPLAY_VIEWS.map((tab) => {
          const badge = badges[tab.value];
          return (
            <button key={tab.value} type="button" aria-current={tab.value === view ? 'page' : undefined}
              className={`segmented__option${tab.value === view ? ' is-active' : ''}`} onClick={() => goToView(game.id, tab.value)}>
              {tab.label}
              {badge && badge.count > 0 && (
                <span className="replay-tabs__badge">{badge.count}<span className="sr-only"> {badge.noun}</span></span>
              )}
            </button>
          );
        })}
      </div>
    </nav>
  );
}

interface GameHubProps {
  gameId: number;
  view: ReplayView;
}

/** The league shell: header, the one primary button, what just happened, the tabs and the current screen. */
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
        <PageHeader title="Replay League" />
        <AsyncView state={loaded} loading={<Skeleton lines={5} label="Loading your league…" />}
          renderError={(error, retry) => (
            <>
              <ErrorState error={error} title="Couldn't open this league" onRetry={retry} />
              <p><a href={buildHash(paths.replay)}>Back to your leagues</a></p>
            </>
          )}>
          {() => null}
        </AsyncView>
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
      <PageHeader
        title={game.name}
        subtitle={`${game.season} season · ${weekText(game)} · ${game.settings.teams} teams · ${scoringLabel(game.settings.scoring)}`}
        actions={(
          <>
            <a className="btn btn--ghost" href={buildHash(paths.replay)}>All leagues</a>
            <button type="button" className="btn btn--primary replay-advance" disabled={isBusy || step === 'done'} onClick={press}>
              {isAdvancing ? pendingLabel : label}
            </button>
          </>
        )}
      />
      <Scoreline game={game} />
      <ChampionBanner game={game} />
      {actError && <p className="error-text replay-alert" role="alert">{actError}</p>}
      <p className="sr-only" role="status">{announce(game, events)}</p>
      {events.length > 0 && <EventsPanel key={events[0].id} events={events} onDismiss={dismissEvents} />}
      {!isDraft && <Tabs game={game} view={view} />}
      <section ref={viewRef} className="replay-view" tabIndex={-1} aria-label={viewLabel}>
        <View game={game} version={version} act={act} isBusy={isBusy} actError={actError} openPlayer={setPlayerId} />
      </section>
      {playerId !== null && <PlayerDrawer key={playerId} gameId={game.id} playerId={playerId} version={version} onClose={closePlayer} />}
    </>
  );
}
