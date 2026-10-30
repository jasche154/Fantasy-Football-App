import { getStreaming } from '../../api/data';
import type { MyLeague, StreamingGrid } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { PlayerLink } from '../../components/PlayerLink';
import { EmptyState, TableSkeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { EMPTY_VALUE, formatNumber } from '../../lib/format';

const TITLES: Record<string, string> = { DST: 'Defenses', QB: 'Quarterbacks' };

function heat(points: number, best: number): string {
  if (best <= 0) return '';
  const share = points / best;
  return share >= 0.9 ? ' heat--high' : share >= 0.7 ? ' heat--mid' : ' heat--low';
}

function Grid({ position, grid, weeks }: { position: string; grid: StreamingGrid; weeks: number[] }) {
  const best = Math.max(0, ...grid.rows.flatMap((r) => Object.values(r.by_week).map((c) => c.projection)));
  return (
    <section aria-label={`${TITLES[position] ?? position} streaming`}>
      <h2 className="section-title">{TITLES[position] ?? position}</h2>
      {grid.path.length > 0 && <p className="streaming-path"><strong>Plan:</strong> {grid.path.join(' · ')}</p>}
      <div className="table-wrap">
        <table className="data-table">
          <caption className="sr-only">{TITLES[position] ?? position} for the next weeks</caption>
          <thead>
            <tr>
              <th scope="col">Player</th>
              {weeks.map((w) => {
                const early = grid.rows.some((r) => r.by_week[String(w)]?.early);
                return <th key={w} scope="col" className="num">Week {w}{early && <span className="source-rank">early</span>}</th>;
              })}
              {position === 'QB' && <th scope="col" className="num" title="Rushing fantasy points per game this season">Rush/g</th>}
              <th scope="col" className="num">Total</th>
            </tr>
          </thead>
          <tbody>
            {grid.rows.map((row) => (
              <tr key={row.player.id} className={row.mine ? 'row--mine' : undefined}>
                <td className="td--player"><PlayerLink player={row.player} />{row.mine && <span className="source-rank">yours</span>}</td>
                {weeks.map((w) => {
                  const cell = row.by_week[String(w)];
                  return (
                    <td key={w} className={`num${cell && !cell.bye ? heat(cell.projection, best) : ' num--muted'}`}>
                      {!cell ? EMPTY_VALUE : cell.bye ? 'BYE' : (
                        <>{formatNumber(cell.projection)}<span className="source-rank">vs {cell.opponent}</span></>
                      )}
                    </td>
                  );
                })}
                {position === 'QB' && <td className="num">{formatNumber(row.rush_ppg ?? 0)}</td>}
                <td className="num"><strong>{formatNumber(row.total)}</strong></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** Defense (and one-QB-league quarterback) streaming for the next three weeks, with a suggested path. */
export function StreamingView({ league }: { league: MyLeague }) {
  const data = useAsync((signal) => getStreaming(league.id, signal), [league.id]);
  return (
    <AsyncView state={data} loading={<TableSkeleton rows={8} columns={5} label="Projecting the next three weeks…" />}
      isEmpty={(d) => Object.keys(d.grids).length === 0} empty={<EmptyState title="Nothing to stream yet." />}>
      {(d) => (
        <>
          <p className="hint">
            Your player and the best free agents. {d.note} The plan only switches when a pickup adds 2+ points that week.
          </p>
          {Object.entries(d.grids).map(([position, grid]) => (
            <Grid key={position} position={position} grid={grid} weeks={d.weeks} />
          ))}
        </>
      )}
    </AsyncView>
  );
}
