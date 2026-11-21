import type { ReplaySummary } from '../../api/types';
import { ThemeToggle } from '../../layout/ThemeToggle';
import { buildHash, paths } from '../../router/routes';
import { scoringLabel } from '../../state/leagueSettings';
import { ScoreNumber, useFinalArrived } from './gameUi';
import { formatRecord, ordinal } from './shared';

function weekChip(game: ReplaySummary): string {
  if (game.phase === 'draft') return 'Draft';
  return game.phase === 'complete' ? 'Final' : `Week ${game.week}`;
}

/** YOU x – y OPP: projections until the week is final. */
function Score({ game }: { game: ReplaySummary }) {
  const { matchup } = game;
  const isFinal = matchup != null && matchup.you.points != null && matchup.opponent.points != null;
  const arrived = useFinalArrived(String(matchup?.week ?? ''), isFinal);
  if (game.phase === 'draft') return null;
  if (!matchup) return <p className="scorebug__game scorebug__idle">{game.phase === 'complete' ? 'Season over' : 'No game this week'}</p>;
  const mine = isFinal ? matchup.you.points : matchup.you.projected;
  const theirs = isFinal ? matchup.opponent.points : matchup.opponent.projected;
  return (
    <div className="scorebug__game">
      <p className="game-label scorebug__status">{matchup.label ?? `Week ${matchup.week}`} · {isFinal ? 'Final' : 'Projected'}</p>
      <p className="scorebug__score">
        <span className="scorebug__side" aria-hidden="true">You</span>
        <ScoreNumber size="sm" value={mine} label={isFinal ? 'Your score' : 'You, projected'} countUp={arrived} />
        <span className="scorebug__dash" aria-hidden="true">–</span>
        <ScoreNumber size="sm" value={theirs} label={`${matchup.opponent.name}${isFinal ? '' : ', projected'}`} countUp={arrived} />
        <span className="scorebug__side" aria-hidden="true">Opp</span>
      </p>
      <p className="scorebug__vs">vs {matchup.opponent.name} ({formatRecord(matchup.opponent.record)})</p>
    </div>
  );
}

interface ScorebugProps {
  /** Null while the league loads (or failed to): Exit, a heading and the theme toggle only. */
  game: ReplaySummary | null;
  advanceLabel?: string;
  isAdvanceDisabled?: boolean;
  onAdvance?: () => void;
}

/** The sticky top of the game: Exit, the league, the week, this week's score, the record and the one primary button. */
export function Scorebug({ game, advanceLabel, isAdvanceDisabled = false, onAdvance }: ScorebugProps) {
  const mine = game?.teams.find((team) => team.team_id === game.user_team_id);
  return (
    <div className="scorebug">
      <a className="scorebug__exit" href={buildHash(paths.replay)}>
        Exit{' '}<span className="sr-only">to your leagues</span>
      </a>
      <div className="scorebug__league">
        {/* Kept in the same place while loading, so focus on it survives the league arriving. */}
        <h1 className="scorebug__title" tabIndex={-1} data-page-heading>{game?.name ?? 'Replay League'}</h1>
        {game && <p className="scorebug__meta">{game.season} season · {game.settings.teams} teams · {scoringLabel(game.settings.scoring)}</p>}
      </div>
      {game && <p className="scorebug__week">{weekChip(game)}</p>}
      {game && <Score game={game} />}
      {game && game.phase !== 'draft' && (
        <p className="scorebug__record">
          <span className="game-label">{mine?.name ?? 'Record'}</span>
          <span className="scorebug__record-line">{formatRecord(game.record)} · {ordinal(game.record.rank)} of {game.settings.teams}</span>
        </p>
      )}
      <div className="scorebug__theme"><ThemeToggle /></div>
      {game && onAdvance && (
        <button type="button" className="btn btn--primary scorebug__advance" disabled={isAdvanceDisabled} onClick={onAdvance}>
          {advanceLabel}
        </button>
      )}
    </div>
  );
}
