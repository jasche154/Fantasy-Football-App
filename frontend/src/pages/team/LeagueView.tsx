import { getLeagueOverview } from '../../api/data';
import type { ManagerTendencies, MyLeague } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { EmptyState, TableSkeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { EMPTY_VALUE, formatNumber } from '../../lib/format';

function percent(value: number | undefined): string {
  return value === undefined ? EMPTY_VALUE : `${Math.round(value * 100)}%`;
}

export function TendencyNote({ habits }: { habits: ManagerTendencies | null }) {
  if (!habits) return null;
  const parts = [
    `${habits.trades} trade${habits.trades === 1 ? '' : 's'}, ${habits.moves} move${habits.moves === 1 ? '' : 's'}`,
    habits.trades_for.length > 0 ? `trades for ${habits.trades_for.join('/')}` : null,
    habits.picks_up.length > 0 ? `picks up ${habits.picks_up.join('/')}` : null,
  ].filter(Boolean);
  return (
    <span className="tendency">
      {habits.label && <span className="status-badge status-badge--ok">{habits.label}</span>} {parts.join(' · ')}
    </span>
  );
}

/** Power rankings with simulated playoff odds, and how each manager trades and works waivers. */
export function LeagueView({ league }: { league: MyLeague }) {
  const data = useAsync((signal) => getLeagueOverview(league.id, signal), [league.id]);
  return (
    <AsyncView state={data} loading={<TableSkeleton rows={10} columns={6} label="Simulating the rest of the season…" />}
      isEmpty={(d) => d.teams.length === 0} empty={<EmptyState title="No standings yet." />}>
      {(d) => (
        <section aria-label="Power rankings">
          <p className="hint">
            Ranked by each team's best lineup in rest-of-season points per game. Playoff odds simulate the last{' '}
            {d.regular_season_weeks_left} regular-season weeks {d.simulations.toLocaleString()} times from Yahoo's
            schedule; the top {d.playoff_teams} make it. Kickers and defenses aren't counted.
          </p>
          <div className="table-wrap">
            <table className="data-table">
              <caption className="sr-only">Power rankings for {d.league}</caption>
              <thead>
                <tr>
                  <th scope="col" className="num">#</th>
                  <th scope="col">Team</th>
                  <th scope="col">Record</th>
                  <th scope="col" className="num" title="Points for this season">PF</th>
                  <th scope="col" className="num" title="Best lineup's rest-of-season points per game">Strength</th>
                  <th scope="col" className="num" title="Average strength of remaining opponents">Opp. left</th>
                  <th scope="col" className="num">Playoff odds</th>
                  <th scope="col" className="num" title="Expected wins by the end of the regular season">Proj. wins</th>
                  <th scope="col">Tendencies</th>
                </tr>
              </thead>
              <tbody>
                {d.teams.map((row) => (
                  <tr key={row.team_key} className={row.is_users_team ? 'row--mine' : undefined}>
                    <td className="num">{row.power_rank}</td>
                    <td>{row.team}{row.is_users_team && <span className="source-rank">you</span>}</td>
                    <td>{row.record}</td>
                    <td className="num">{formatNumber(row.points_for)}</td>
                    <td className="num">{row.strength === null ? EMPTY_VALUE : formatNumber(row.strength)}</td>
                    <td className="num">{row.remaining_opponent_strength === null ? EMPTY_VALUE : formatNumber(row.remaining_opponent_strength)}</td>
                    <td className="num"><strong>{percent(row.playoff_odds)}</strong></td>
                    <td className="num">{row.expected_wins === undefined ? EMPTY_VALUE : formatNumber(row.expected_wins)}</td>
                    <td><TendencyNote habits={row.tendencies} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="hint">Active traders are the managers to send trade offers to first.</p>
        </section>
      )}
    </AsyncView>
  );
}
