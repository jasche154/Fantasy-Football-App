import { getReplayStandings } from '../../api/replay';
import type { ReplayStandings } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { Icon } from '../../components/Icon';
import { TableSkeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { GameTable } from './LeagueScoreboard';
import { formatPoints, formatRecord, type ReplayViewProps } from './shared';

function StandingsTable({ standings }: { standings: ReplayStandings }) {
  const { results, playoff_teams: playoffTeams, playoff_weeks: playoffWeeks, byes, seeds_final: seedsFinal } = standings;
  const isOver = results.some((row) => row.place !== null);         // rows are then in final order
  return (
    <>
      <div className="table-wrap">
        <table className="data-table">
          <caption className="sr-only">League standings</caption>
          <thead>
            <tr>
              <th scope="col" className="num">{isOver ? 'Finish' : 'Seed'}</th>
              <th scope="col">Team</th>
              <th scope="col">W-L-T</th>
              <th scope="col" className="num" title="Points for">PF</th>
              <th scope="col" className="num" title="Points against">PA</th>
            </tr>
          </thead>
          <tbody>
            {results.map((row, index) => {
              const classes = [row.is_user ? 'row--mine' : '', index === playoffTeams - 1 ? 'replay-standings__cut' : ''].filter(Boolean).join(' ');
              return (
                <tr key={row.team_id} className={classes || undefined}>
                  <td className="num">{row.place ?? row.seed}</td>
                  <td>
                    {row.name}{row.is_user && <span className="source-rank">you</span>}
                    {/* Only once the regular season is over is a top seed really in. */}
                    {seedsFinal && row.in_playoffs && <span className="status-badge status-badge--ok replay-league__badge">In playoffs</span>}
                  </td>
                  <td>{formatRecord(row)}</td>
                  <td className="num">{formatPoints(row.points_for)}</td>
                  <td className="num">{formatPoints(row.points_against)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="hint">
        The line is the playoff cut: the top {playoffTeams} seeds play for the title in weeks {playoffWeeks[0]}-{playoffWeeks.at(-1)}
        {byes > 0 && `, and the top ${byes} skip${byes === 1 ? 's' : ''} the first round`}.{' '}
        {seedsFinal ? 'The seeds are final.' : `Seeds lock after week ${standings.regular_season_weeks}.`}
      </p>
    </>
  );
}

const TITLE_GAMES: readonly (string | null)[] = ['Quarter-final', 'Semi-final', 'Championship', 'Third place'];

/** The playoff weeks cut to the title games or to the rest (the consolation games that decide the places below). */
function PlayoffWeeks({ bracket, isTitle, kind }: { bracket: ReplayStandings['bracket']; isTitle: boolean; kind: string }) {
  return bracket.map((board) => {
    const results = board.results.filter((game) => TITLE_GAMES.includes(game.label) === isTitle);
    if (results.length === 0) return null;
    return (
      <div key={board.week}>
        <h3 className="replay-bracket__week">Week {board.week}</h3>
        <GameTable caption={`Week ${board.week} ${kind} games`} board={{ ...board, results }} />
      </div>
    );
  });
}

/** The playoff games known so far (quarter- and semi-finals, then the championship and third place), the champion, then the consolation games. */
function Bracket({ standings }: { standings: ReplayStandings }) {
  const { bracket, champion } = standings;
  if (bracket.length === 0) return null;
  const hasConsolation = bracket.some((board) => board.results.some((game) => !TITLE_GAMES.includes(game.label)));
  return (
    <>
      <section aria-label="Playoff bracket">
        <h2 className="section-title">Playoffs</h2>
        {champion && (
          <p className="replay-bracket__champion"><Icon name="trophy" size={18} /> Champion: <strong>{champion.name}</strong></p>
        )}
        <PlayoffWeeks bracket={bracket} isTitle kind="playoff" />
      </section>
      {hasConsolation && (
        <section aria-label="Consolation games">
          <h2 className="section-title">Consolation</h2>
          <p className="hint">Teams out of the title race play for the places below it; the final standings follow these games.</p>
          <PlayoffWeeks bracket={bracket} isTitle={false} kind="consolation" />
        </section>
      )}
    </>
  );
}

/** The table by seed (final order once the season is over) and, once they start, the playoffs. */
export function LeagueStandings({ game, version }: ReplayViewProps) {
  const standings = useAsync((signal) => getReplayStandings(game.id, signal), [game.id, version]);
  return (
    <AsyncView state={standings} loading={<TableSkeleton rows={game.settings.teams} columns={5} label="Loading the standings…" />}
      errorTitle="Couldn't load the standings">
      {(data) => (
        <>
          <StandingsTable standings={data} />
          <Bracket standings={data} />
        </>
      )}
    </AsyncView>
  );
}
