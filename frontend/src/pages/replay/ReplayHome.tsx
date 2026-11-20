import { useEffect, useRef, useState } from 'react';
import { errorMessage } from '../../api/client';
import { deleteReplayGame, getReplayGames } from '../../api/replay';
import type { ReplayGameListing } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { Card, PageHeader } from '../../components/Card';
import { EmptyState, TableSkeleton } from '../../components/StatusViews';
import { useAction } from '../../hooks/useAction';
import { useAsync } from '../../hooks/useAsync';
import { useDocumentTitle } from '../../hooks/useDocumentTitle';
import { EMPTY_VALUE } from '../../lib/format';
import { buildHash, paths } from '../../router/routes';
import { navigate } from '../../router/useHashLocation';
import { scoringLabel } from '../../state/leagueSettings';
import { SetupForm } from './SetupForm';
import { ConfirmButton } from './shared';

function statusText(game: ReplayGameListing): string {
  if (game.phase === 'draft') return 'Drafting';
  if (game.phase === 'complete') return 'Season complete';
  return game.phase === 'waivers' ? `Week ${game.week} · waivers` : `Week ${game.week}`;
}

interface SavedGamesProps {
  games: readonly ReplayGameListing[];
  isDeleting: boolean;
  onDelete: (game: ReplayGameListing) => void;
}

function SavedGames({ games, isDeleting, onDelete }: SavedGamesProps) {
  return (
    <div className="table-wrap">
      <table className="data-table">
        <caption className="sr-only">Your saved leagues</caption>
        <thead>
          <tr>
            <th scope="col">League</th>
            <th scope="col" className="num">Season</th>
            <th scope="col">Status</th>
            <th scope="col">Champion</th>
            <th scope="col"><span className="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          {games.map((game) => (
            <tr key={game.id}>
              <th scope="row" className="td--player">
                {game.name}
                <span className="replay-saved__meta">{game.teams} teams · {scoringLabel(game.scoring)}</span>
              </th>
              <td className="num">{game.season}</td>
              <td>{statusText(game)}</td>
              <td>{game.champion ?? EMPTY_VALUE}</td>
              <td>
                <span className="replay-saved__actions">
                  <a className="btn btn--primary btn--sm" href={buildHash(paths.replay, { game: game.id })} aria-label={`Resume ${game.name}`}>
                    Resume
                  </a>
                  <ConfirmButton label="Delete" confirmLabel="Yes, delete" subject={game.name} disabled={isDeleting} onConfirm={() => onDelete(game)} />
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** #/replay: the saved leagues (resume, delete) and the new-league form. */
export function ReplayHome() {
  useDocumentTitle('Replay League');
  const games = useAsync((signal) => getReplayGames(signal), []);
  const remove = useAction(deleteReplayGame);
  // An object, so deleting a second league refocuses the notice.
  const [deleted, setDeleted] = useState<{ name: string } | null>(null);
  const noticeRef = useRef<HTMLParagraphElement>(null);
  const onDelete = async (game: ReplayGameListing) => {
    if (!(await remove.run(game.id))) return;
    setDeleted({ name: game.name });
    games.reload();
  };
  // Off while the list reloads too: the league just deleted is still in it until the new list arrives.
  const isDeleting = remove.status === 'pending' || games.status === 'loading';
  // The pressed button leaves with its row: put focus on the line that says what happened.
  useEffect(() => {
    if (deleted) noticeRef.current?.focus({ preventScroll: true });
  }, [deleted]);
  return (
    <>
      <PageHeader title="Replay League"
        subtitle="Draft against CPU managers in a past NFL season, then play it out week by week with the real results." />
      <Card title="Your leagues">
        {remove.status === 'error' && <p className="error-text" role="alert">{errorMessage(remove.error)}</p>}
        <p ref={noticeRef} tabIndex={-1} role="status" className="success-text replay-notice">{deleted && `Deleted ${deleted.name}.`}</p>
        <AsyncView state={games} loading={<TableSkeleton rows={2} columns={4} label="Loading your leagues…" />}
          isEmpty={(data) => data.results.length === 0} empty={<EmptyState title="No leagues yet.">Start one below.</EmptyState>}>
          {(data) => <SavedGames games={data.results} isDeleting={isDeleting} onDelete={onDelete} />}
        </AsyncView>
      </Card>
      <Card title="New league">
        <SetupForm onCreated={(game) => navigate(paths.replay, { game: game.id })} />
      </Card>
    </>
  );
}
