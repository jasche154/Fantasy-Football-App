import { getReplayStandings } from '../../api/replay';
import type { ReplayStandings } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { TableSkeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { TeamBadge } from './gameUi';
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
                    <TeamBadge team={row} showTendency />
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

/** The table by seed (final order once the season is over). The bracket is on the Playoffs page. */
export function LeagueStandings({ game, version }: ReplayViewProps) {
  const standings = useAsync((signal) => getReplayStandings(game.id, signal), [game.id, version]);
  return (
    <AsyncView state={standings} loading={<TableSkeleton rows={game.settings.teams} columns={5} label="Loading the standings…" />}
      errorTitle="Couldn't load the standings">
      {(data) => <StandingsTable standings={data} />}
    </AsyncView>
  );
}
