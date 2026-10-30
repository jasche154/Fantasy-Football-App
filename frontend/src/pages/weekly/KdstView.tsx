import { getKdst } from '../../api/data';
import type { KdstRow } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { EmptyState, TableSkeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { formatNumber } from '../../lib/format';

function Details({ row }: { row: KdstRow }) {
  return (
    <details className="kdst-why">
      <summary>Why?</summary>
      <ul className="reason-list">{row.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
    </details>
  );
}

/** Kicker or defense projections for the week, with the inputs behind each. */
export function KdstView({ position, week }: { position: 'K' | 'DST'; week: number | undefined }) {
  const data = useAsync((signal) => getKdst(position, week, signal), [position, week]);
  const isKicker = position === 'K';
  return (
    <AsyncView state={data} loading={<TableSkeleton rows={10} columns={6} label="Projecting…" />}
      isEmpty={(d) => d.results.length === 0} empty={<EmptyState title="No games this week." />}>
      {(d) => (
        <div className="table-wrap table-wrap--tall">
          <table className="data-table">
            <caption className="sr-only">{isKicker ? 'Kicker' : 'Defense'} projections, week {d.week}</caption>
            <thead>
              <tr>
                <th scope="col" className="num">Rank</th>
                <th scope="col">{isKicker ? 'Kicker' : 'Defense'}</th>
                <th scope="col">Opp</th>
                <th scope="col" className="num">Proj</th>
                {isKicker ? (
                  <>
                    <th scope="col" className="num" title="Expected field-goal tries">FGA</th>
                    <th scope="col" className="num" title="Expected field goals made">FGM</th>
                    <th scope="col" className="num" title="Expected extra points">XP</th>
                    <th scope="col" title="Kickoff forecast (outdoor games)">Weather</th>
                  </>
                ) : (
                  <>
                    <th scope="col" className="num">Sacks</th>
                    <th scope="col" className="num" title="Interceptions + fumble recoveries">Takeaways</th>
                    <th scope="col" className="num" title="Opponent's Vegas implied points">Opp total</th>
                  </>
                )}
                <th scope="col"><span className="sr-only">Details</span></th>
              </tr>
            </thead>
            <tbody>
              {d.results.map((row) => (
                <tr key={`${row.player.team}-${row.rank}`}>
                  <td className="num td--rank">{row.rank}</td>
                  <td className="td--player"><strong>{row.player.name}</strong> <span className="player-cell__team">{row.player.team}</span></td>
                  <td>{row.opponent}</td>
                  <td className="num"><span className="value-pill">{formatNumber(row.projection)}</span></td>
                  {isKicker ? (
                    <>
                      <td className="num">{formatNumber(row.fg_attempts ?? null)}</td>
                      <td className="num">{formatNumber(row.fg_made ?? null)}</td>
                      <td className="num">{formatNumber(row.pat_made ?? null)}</td>
                      <td>{row.indoors ? 'Indoors' : row.wind != null ? `${Math.round(row.wind)} mph, ${Math.round(row.temp ?? 0)}°F` : '—'}</td>
                    </>
                  ) : (
                    <>
                      <td className="num">{formatNumber(row.sacks ?? null)}</td>
                      <td className="num">{formatNumber(row.takeaways ?? null)}</td>
                      <td className="num">{formatNumber(row.opp_implied ?? null)}</td>
                    </>
                  )}
                  <td><Details row={row} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="hint">Yahoo default scoring. My Team uses your league's own kicker and defense scoring.</p>
        </div>
      )}
    </AsyncView>
  );
}
