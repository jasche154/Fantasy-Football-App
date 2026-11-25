import { useState } from 'react';
import { getReplayStandings } from '../../api/replay';
import type { ReplayStandings } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { nextSort, SortableHeader, type SortState } from '../../components/SortableHeader';
import { TableSkeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { TeamBadge } from './gameUi';
import { formatPoints, formatRecord, type ReplayViewProps } from './shared';

type StandingsSort = 'order' | 'points_for' | 'points_against';
const DEFAULT_SORT: SortState<StandingsSort> = { key: 'order', direction: 'asc' };

function StandingsTable({ standings }: { standings: ReplayStandings }) {
  const { playoff_teams: playoffTeams, playoff_weeks: playoffWeeks, byes, seeds_final: seedsFinal } = standings;
  const isOver = standings.results.some((row) => row.place !== null);         // rows are then in final order
  // The server's order (record, then tiebreaks) is the default; Seed/Finish brings it back.
  const [sort, setSort] = useState(DEFAULT_SORT);
  const onSort = (key: StandingsSort) => setSort(nextSort(sort, key, (column) => (column === 'order' ? 'asc' : 'desc')));
  const ordered = standings.results.map((row, index) => ({ row, index }));
  const column = sort.key === 'order' ? null : sort.key;
  const sign = sort.direction === 'desc' ? -1 : 1;
  if (column) ordered.sort((a, b) => sign * (a.row[column] - b.row[column]) || a.index - b.index);   // ties keep standings order
  else if (sign < 0) ordered.reverse();
  const isDefault = sort.key === 'order' && sort.direction === 'asc';
  return (
    <>
      <div className="table-wrap">
        <table className="data-table">
          <caption className="sr-only">League standings</caption>
          <thead>
            <tr>
              <SortableHeader columnKey="order" sort={sort} onSort={onSort} title="Standings order (record, then tiebreaks)">{isOver ? 'Finish' : 'Seed'}</SortableHeader>
              <th scope="col">Team</th>
              <th scope="col">W-L-T</th>
              <SortableHeader columnKey="points_for" sort={sort} onSort={onSort} title="Points for">PF</SortableHeader>
              <SortableHeader columnKey="points_against" sort={sort} onSort={onSort} title="Points against">PA</SortableHeader>
            </tr>
          </thead>
          <tbody>
            {ordered.map(({ row, index }) => {
              // The playoff cut only means something in standings order.
              const classes = [row.is_user ? 'row--mine' : '', isDefault && index === playoffTeams - 1 ? 'replay-standings__cut' : ''].filter(Boolean).join(' ');
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
      {!isDefault && <p className="hint">Sorted by {sort.key === 'order' ? 'standings order, reversed' : sort.key === 'points_for' ? 'points for' : 'points against'}. Select {isOver ? 'Finish' : 'Seed'} to go back to the standings order.</p>}
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
