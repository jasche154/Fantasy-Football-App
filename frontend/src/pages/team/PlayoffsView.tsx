import { getPlayoffs } from '../../api/data';
import type { MyLeague, PlayoffFix, PlayoffFixOption } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { PlayerLink } from '../../components/PlayerLink';
import { EmptyState, TableSkeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { formatNumber } from '../../lib/format';
import { factorTone } from '../../lib/matchup';

function FixOptions({ title, options }: { title: string; options: readonly PlayoffFixOption[] }) {
  if (options.length === 0) return null;
  return (
    <p className="playoff-fix__options">
      <span className="playoff-fix__kind">{title}:</span>{' '}
      {options.map((o, index) => (
        <span key={o.player.id}>
          {index > 0 && ', '}
          <PlayerLink player={o.player} /> {formatNumber(o.playoff_total)} (+{formatNumber(o.gain)}){o.owner && ` on ${o.owner}`}
        </span>
      ))}
    </p>
  );
}

/** Upgrades over your weakest starter at each position, totalled across the playoff weeks. */
function PlayoffPlanner({ fixes }: { fixes: readonly PlayoffFix[] }) {
  return (
    <section className="playoff-plan" aria-label="Playoff planner">
      <h2 className="section-title">Playoff planner</h2>
      {fixes.length === 0 ? (
        <p className="hint">No free agent or trade target beats your starters by 3+ points over the playoff weeks.</p>
      ) : (
        <ul className="playoff-fix-list">
          {fixes.map((fix) => (
            <li key={fix.position} className="playoff-fix">
              <p className="playoff-fix__head">
                <strong>{fix.position}</strong>
                {fix.weakest_starter && <> · weakest starter {fix.weakest_starter.name}, {formatNumber(fix.weakest_starter.playoff_total)} playoff points</>}
              </p>
              <FixOptions title="Pick up" options={fix.waivers} />
              <FixOptions title="Trade for" options={fix.trade_targets} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Your roster across the fantasy-playoff weeks: opponents, byes, projections, and the best lineup each week. */
export function PlayoffsView({ league }: { league: MyLeague }) {
  const data = useAsync((signal) => getPlayoffs(league.id, signal), [league.id]);
  return (
    <AsyncView state={data} loading={<TableSkeleton rows={10} columns={5} label="Looking ahead to the playoffs…" />}
      isEmpty={(d) => d.players.length === 0} empty={<EmptyState title="No roster imported yet." />}>
      {(d) => (
        <section aria-label="Playoff outlook">
          <p className="hint">
            Playoff weeks {d.weeks.join(', ')} ({d.source === 'league' ? "your league's schedule" : 'default'}).
            Projections are rest-of-season points per game adjusted for each week's opponent; kickers and defenses aren't projected this far out.
          </p>
          <div className="table-wrap">
            <table className="data-table">
              <caption className="sr-only">Playoff weeks by player</caption>
              <thead>
                <tr>
                  <th scope="col">Player</th>
                  <th scope="col" className="num">ROS PPG</th>
                  {d.weeks.map((w) => <th key={w} scope="col" className="num">Week {w}</th>)}
                  <th scope="col" className="num">Total</th>
                </tr>
              </thead>
              <tbody>
                {d.players.map((row) => (
                  <tr key={row.player.id}>
                    <td className="td--player"><PlayerLink player={row.player} /> <span className="player-cell__team">{row.slot}</span></td>
                    <td className="num">{formatNumber(row.ros_ppg)}</td>
                    {row.weeks.map((c) => (
                      <td key={c.week} className={`num${c.bye ? ' num--muted' : ''}`}>
                        {c.bye ? 'BYE' : (
                          <>
                            {formatNumber(c.projection)}
                            <span className={`source-rank sos-text sos-text--${factorTone(c.factor ?? 1)}`}>vs {c.opponent}</span>
                          </>
                        )}
                      </td>
                    ))}
                    <td className="num"><strong>{formatNumber(row.playoff_total)}</strong></td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <th scope="row">Best lineup</th>
                  <td />
                  {d.weeks.map((w) => {
                    const l = d.lineups[String(w)];
                    return (
                      <td key={w} className={`num${d.weakest_week === w ? ' playoff-weak' : ''}`}>
                        <strong>{formatNumber(l.projected)}</strong>
                        {l.empty_slots.length > 0 && <span className="source-rank">empty: {l.empty_slots.join(', ')}</span>}
                      </td>
                    );
                  })}
                  <td />
                </tr>
              </tfoot>
            </table>
          </div>
          {d.weakest_week && <p>Your weakest playoff week is <strong>week {d.weakest_week}</strong>: target players with good matchups that week.</p>}
          {d.fixes && <PlayoffPlanner fixes={d.fixes} />}
        </section>
      )}
    </AsyncView>
  );
}
