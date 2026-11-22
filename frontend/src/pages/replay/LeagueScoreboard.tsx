import { useState } from 'react';
import { getReplayScoreboard } from '../../api/replay';
import type { ReplayScoreboard, ReplaySide } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { SelectField } from '../../components/SelectField';
import { EmptyState, Skeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { TeamBadge } from './gameUi';
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

const STATUS: Readonly<Record<Scores, string>> = { final: 'Final', projected: 'Projected', none: 'Upcoming' };

function TileSide({ side, scores, isWinner }: { side: ReplaySide; scores: Scores; isWinner: boolean }) {
  return (
    <p className={isWinner ? 'game-tile__side is-winner' : 'game-tile__side'}>
      <TeamBadge team={side} />
      {isWinner && <span className="game-tile__won">Won</span>}
      {scores !== 'none' && <span className="game-tile__score">{formatPoints(scores === 'final' ? side.points : side.projected)}</span>}
    </p>
  );
}

/** One week's games as scoreboard tiles. Never shows a number the API did not send for that week. */
export function GameTiles({ board, caption }: { board: ReplayScoreboard; caption: string }) {
  const scores = scoresOf(board);
  return (
    <ul className="game-tiles" aria-label={caption}>
      {board.results.map((game) => (
        <li key={game.home.team_id} className={game.home.is_user || game.away.is_user ? 'game-tile game-tile--mine' : 'game-tile'}>
          <p className="game-label game-tile__head">{game.label ? `${game.label} · ` : ''}{STATUS[scores]}</p>
          <TileSide side={game.home} scores={scores} isWinner={game.winner === game.home.team_id} />
          <TileSide side={game.away} scores={scores} isWinner={game.winner === game.away.team_id} />
        </li>
      ))}
    </ul>
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
      <AsyncView state={board} loading={<Skeleton lines={6} label="Loading the scoreboard…" />}
        errorTitle="Couldn't load the scoreboard" isEmpty={(data) => data.results.length === 0}
        empty={<EmptyState title="No games set yet">The pairings for week {week} depend on results still to come.</EmptyState>}>
        {(data) => (
          <>
            <p className="hint">Week {data.week}. {SCORE_NOTES[scoresOf(data)]}</p>
            <GameTiles board={data} caption={`Week ${data.week} scoreboard`} />
          </>
        )}
      </AsyncView>
    </>
  );
}
