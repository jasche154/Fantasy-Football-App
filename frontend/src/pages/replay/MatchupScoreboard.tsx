import type { ReplayGameRow, ReplayScoreboard, ReplaySide } from '../../api/types';
import { EmptyState } from '../../components/StatusViews';
import { formatPoints } from './shared';

function ScoreLine({ side, game, isPlayed }: { side: ReplaySide; game: ReplayGameRow; isPlayed: boolean }) {
  const isWinner = game.winner === side.team_id;
  return (
    <span className={`replay-scoreboard__side${isWinner ? ' is-winner' : ''}`}>
      <span>{side.name}{side.is_user && ' (you)'}{isWinner && <span className="sr-only"> (won)</span>}</span>
      {/* A week that is not played only ever shows projections. */}
      <span className="replay-scoreboard__score">{formatPoints(isPlayed ? side.points : side.projected)}</span>
    </span>
  );
}

interface MatchupScoreboardProps {
  board: ReplayScoreboard;
  /** The home team of the game on screen above. */
  shownTeamId: number | undefined;
  onShow: (teamId: number) => void;
}

/** Every game of the week; pressing one shows it in the face-off above. */
export function MatchupScoreboard({ board, shownTeamId, onShow }: MatchupScoreboardProps) {
  return (
    <section aria-label={`Week ${board.week} scoreboard`}>
      <h2 className="section-title">Week {board.week} scoreboard <span className="matchup-side__total">{board.played ? 'Final' : 'Projected'}</span></h2>
      {board.results.length === 0 ? <EmptyState title="No games this week." /> : (
        <ul className="replay-scoreboard">
          {board.results.map((game) => (
            <li key={game.home.team_id}>
              <button type="button" className="replay-scoreboard__game" aria-pressed={game.home.team_id === shownTeamId}
                onClick={() => onShow(game.home.team_id)}>
                {game.label && <span className="replay-scoreboard__label">{game.label}</span>}
                <ScoreLine side={game.home} game={game} isPlayed={board.played} />
                <ScoreLine side={game.away} game={game} isPlayed={board.played} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
