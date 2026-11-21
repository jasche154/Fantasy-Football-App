import { useState } from 'react';
import { getReplayScoreboard } from '../../api/replay';
import type { ReplayScoreboard, ReplaySide } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { SelectField } from '../../components/SelectField';
import { EmptyState, TableSkeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { formatPoints, type ReplayViewProps } from './shared';

/** What a week can show: results once played, pre-game projections once reached, else the pairings only. */
type Scores = 'final' | 'projected' | 'none';

function scoresOf(board: ReplayScoreboard): Scores {
  if (board.played) return 'final';
  return board.results.some((game) => game.home.projected !== null || game.away.projected !== null) ? 'projected' : 'none';
}

const SCORE_NOTES: Readonly<Record<Scores, string>> = {
  final: 'Final scores.',
  projected: 'Not played yet: these are pre-game projections.',
  none: 'Not played yet: the pairings only.',
};

function TeamCells({ side, scores, isWinner }: { side: ReplaySide; scores: Scores; isWinner: boolean }) {
  return (
    <>
      <td>
        {isWinner ? <strong>{side.name}</strong> : side.name}
        {isWinner && <span className="status-badge status-badge--ok replay-league__badge">Won</span>}
      </td>
      {scores !== 'none' && <td className="num">{formatPoints(scores === 'final' ? side.points : side.projected)}</td>}
    </>
  );
}

/** One week's games. Never shows a number the API did not send for that week. */
export function GameTable({ board, caption }: { board: ReplayScoreboard; caption: string }) {
  const scores = scoresOf(board);
  const hasLabels = board.results.some((game) => game.label !== null);
  const scoreHead = scores === 'final' ? 'Pts' : 'Proj';
  return (
    <div className="table-wrap">
      <table className="data-table data-table--compact">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {hasLabels && <th scope="col">Game</th>}
            <th scope="col">Team</th>
            {scores !== 'none' && <th scope="col" className="num">{scoreHead}</th>}
            <th scope="col">Opponent</th>
            {scores !== 'none' && <th scope="col" className="num">{scoreHead}</th>}
          </tr>
        </thead>
        <tbody>
          {board.results.map((game) => (
            <tr key={game.home.team_id} className={game.home.is_user || game.away.is_user ? 'row--mine' : undefined}>
              {hasLabels && <td>{game.label}</td>}
              <TeamCells side={game.home} scores={scores} isWinner={game.winner === game.home.team_id} />
              <TeamCells side={game.away} scores={scores} isWinner={game.winner === game.away.team_id} />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Any week of the season: results, projections for the current week, pairings for the weeks ahead. */
export function LeagueScoreboard({ game, version }: ReplayViewProps) {
  const [week, setWeek] = useState(Math.min(Math.max(game.week, 1), game.end_week));
  const board = useAsync((signal) => getReplayScoreboard(game.id, week, signal), [game.id, week, version]);
  const weeks = Array.from({ length: game.end_week }, (_, index) => index + 1);
  const options = weeks.map((value) => ({ value, label: `Week ${value}${value > game.settings.regular_season_weeks ? ' (playoffs)' : ''}` }));
  return (
    <>
      <div className="weekly-controls">
        <SelectField label="Week" isInline value={week} options={options} onChange={(value) => setWeek(Number(value))} />
      </div>
      <AsyncView state={board} loading={<TableSkeleton rows={6} columns={4} label="Loading the scoreboard…" />}
        errorTitle="Couldn't load the scoreboard" isEmpty={(data) => data.results.length === 0}
        empty={<EmptyState title="No games set yet">The pairings for week {week} depend on results still to come.</EmptyState>}>
        {(data) => (
          <>
            <p className="hint">Week {data.week}. {SCORE_NOTES[scoresOf(data)]}</p>
            <GameTable board={data} caption={`Week ${data.week} scoreboard`} />
          </>
        )}
      </AsyncView>
    </>
  );
}
