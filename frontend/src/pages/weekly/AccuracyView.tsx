import { getModelAccuracy } from '../../api/data';
import { AsyncView } from '../../components/AsyncView';
import { EmptyState, TableSkeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { formatNumber } from '../../lib/format';
import { useLeagueSettings } from '../../state/SettingsContext';

const pct = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v * 100)}%`);

/** How the projections have done: this season live, the backtest on held-out seasons, learned weights. */
export function AccuracyView() {
  const { settings } = useLeagueSettings();
  const data = useAsync((signal) => getModelAccuracy(settings.scoring, signal), [settings.scoring]);
  return (
    <AsyncView state={data} loading={<TableSkeleton rows={6} columns={4} label="Scoring the projections…" />}
      isEmpty={() => false} empty={<EmptyState title="No data" />}>
      {(d) => (
        <div className="accuracy">
          <section aria-label="This season">
            <h2 className="section-title">This season (pre-kickoff projections vs what happened)</h2>
            {d.live.n ? (
              <>
                <p>
                  {d.live.n} player-weeks in week{d.live.weeks.length > 1 ? 's' : ''} {d.live.weeks.join(', ')}: average miss{' '}
                  <strong>{formatNumber(d.live.blend_mae)}</strong> points.
                </p>
                <p>
                  Below the floor {pct(d.live.range?.below_floor)} and above the ceiling {pct(d.live.range?.above_ceiling)} of the
                  time (each should be about 10%).
                  {(d.live.range?.below_floor ?? 0) > 0.15 && ' Floors have been too high so far; one or two weeks is a small sample.'}
                </p>
                <ul className="reason-list">
                  {Object.entries(d.live.bias_by_position ?? {}).map(([pos, bias]) => (
                    <li key={pos}>{pos}: projected {bias >= 0 ? `${formatNumber(bias)} too high` : `${formatNumber(-bias)} too low`} on average</li>
                  ))}
                </ul>
              </>
            ) : <p>No completed week with saved projections yet.</p>}
          </section>
          {d.backtest?.held_out && (
            <section aria-label="Backtest">
              <h2 className="section-title">Model backtest on seasons it never saw</h2>
              <table className="data-table">
                <thead><tr><th scope="col">Season</th><th scope="col" className="num">RMSE</th><th scope="col" className="num">MAE</th><th scope="col" className="num">Rank corr.</th></tr></thead>
                <tbody>
                  {Object.entries(d.backtest.held_out).map(([season, r]) => (
                    <tr key={season}>
                      <td>{season}</td>
                      <td className="num">{formatNumber(r.before.rmse, 2)} → <strong>{formatNumber(r.after.rmse, 2)}</strong></td>
                      <td className="num">{formatNumber(r.before.mae, 2)} → <strong>{formatNumber(r.after.mae, 2)}</strong></td>
                      <td className="num">{formatNumber(r.before.rank_corr, 3)} → <strong>{formatNumber(r.after.rank_corr, 3)}</strong></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="hint">Before → after the calibration, EPA matchup and weather adjustments, fit only on earlier seasons.</p>
            </section>
          )}
          <section aria-label="Learned weights">
            <h2 className="section-title">Source weights, learned from results</h2>
            {d.weight_history.length === 0 ? <p>Still on the starting weights.</p> : (
              <ul className="reason-list">
                {d.weight_history.map((h) => (
                  <li key={`${h.season}-${h.week}`}>
                    After week {h.week} ({h.rows} players):{' '}
                    {Object.entries(h.weights).map(([k, v]) => `${k.replace('_', ' ')} ${pct(v)}`).join(', ')}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}
    </AsyncView>
  );
}
