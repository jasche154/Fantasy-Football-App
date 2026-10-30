import { getMatchup } from '../../api/data';
import type { MatchupResponse, MatchupStarter } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { PageHeader } from '../../components/Card';
import { SelectField } from '../../components/SelectField';
import { EmptyState, TableSkeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { useDocumentTitle } from '../../hooks/useDocumentTitle';
import { EMPTY_VALUE, formatNumber } from '../../lib/format';
import { buildHash, paths } from '../../router/routes';
import { useMyLeague } from '../../state/useMyLeague';

const MODE_LABELS: Record<string, string> = { expected: 'Most points', upside: 'Upside', safe: 'Safe' };
const pct = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v * 100)}%`);

function Lineup({ title, rows, total }: { title: string; rows: readonly MatchupStarter[]; total: number }) {
  return (
    <section className="matchup-side" aria-label={title}>
      <h2 className="section-title">{title} <span className="matchup-side__total">{formatNumber(total)}</span></h2>
      <table className="data-table">
        <thead><tr><th scope="col">Slot</th><th scope="col">Player</th><th scope="col" className="num">Proj</th><th scope="col" className="num">Range</th><th scope="col" className="num">Actual</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.slot}-${r.player_id}`}>
              <td className="td--pos">{r.slot}</td>
              <td className="td--player">{r.name}{r.team && <span className="player-cell__team"> {r.team}{r.opponent ? ` vs ${r.opponent}` : ''}</span>}
                {r.status && <span className="status-badge status-badge--missing">{r.status}</span>}</td>
              <td className="num">{formatNumber(r.projection)}</td>
              <td className="num">{r.floor != null && r.ceiling != null ? `${formatNumber(r.floor)}–${formatNumber(r.ceiling)}` : EMPTY_VALUE}</td>
              <td className="num">{r.actual != null ? formatNumber(r.actual) : EMPTY_VALUE}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function Strategy({ data }: { data: MatchupResponse }) {
  const rec = data.recommended;
  return (
    <section className="lineup-advice" aria-label="Lineup strategy">
      <p className="lineup-advice__summary">
        Your current lineup wins <strong>{pct(data.win_probability)}</strong> of {data.simulations.toLocaleString()} simulated weeks
        {data.yahoo_win_probability != null && <> (Yahoo says {pct(data.yahoo_win_probability)})</>}.
      </p>
      <ul className="strategy-list">
        {Object.entries(data.strategies).map(([mode, s]) => (
          <li key={mode} className={mode === rec ? 'is-recommended' : undefined}>
            <strong>{MODE_LABELS[mode] ?? mode}</strong> lineup: {formatNumber(s.projected)} projected, wins {pct(s.win_probability)}
            {mode === rec && <span className="status-badge status-badge--ok">Recommended</span>}
          </li>
        ))}
      </ul>
      {data.changes.start.length > 0 ? (
        <p>To use it: start {data.changes.start.join(', ')}; sit {data.changes.sit.join(', ') || 'nobody'}.</p>
      ) : <p>Your current lineup already is the recommended one.</p>}
      <p className="hint">Underdogs usually win more often with high-ceiling starts, favorites with high floors. Each lineup is simulated against the opponent's.</p>
    </section>
  );
}

export function MatchupPage() {
  useDocumentTitle('Matchup');
  const { leagues, league, choose } = useMyLeague();
  const matchup = useAsync((signal) => (league ? getMatchup(league.id, signal) : Promise.resolve(null)), [league?.id]);
  const options = (leagues.data?.leagues ?? []).map((l) => ({ value: l.id, label: l.name }));
  return (
    <>
      <PageHeader title="Matchup" subtitle={league ? `${league.name} · week ${league.week ?? ''}` : 'Your Yahoo matchup this week'}
        actions={options.length > 1 && league ? <SelectField label="League" isInline value={league.id} options={options} onChange={(v) => choose(Number(v))} /> : undefined} />
      <AsyncView state={leagues} loading={<TableSkeleton rows={4} columns={3} label="Loading your leagues…" />}
        isEmpty={(d) => d.leagues.length === 0}
        empty={<EmptyState title="No Yahoo league imported yet.">Connect Yahoo and import a league in <a href={buildHash(paths.settings)}>Settings</a>.</EmptyState>}>
        {() => (
          <AsyncView state={matchup} loading={<TableSkeleton rows={10} columns={5} label="Simulating your matchup…" />}
            isEmpty={(d) => !d} empty={<EmptyState title="No matchup this week." />}>
            {(d) => d && (
              <>
                <p className="matchup-headline"><strong>{d.me.team}</strong> {formatNumber(d.me.projected)} vs {formatNumber(d.opponent.projected)} <strong>{d.opponent.team}</strong></p>
                <Strategy data={d} />
                <div className="matchup-grid">
                  <Lineup title={`${d.me.team} (you)`} rows={d.me.starters} total={d.me.projected} />
                  <Lineup title={d.opponent.team} rows={d.opponent.starters} total={d.opponent.projected} />
                </div>
                <p className="hint attribution">Fantasy data provided by Yahoo Fantasy.</p>
              </>
            )}
          </AsyncView>
        )}
      </AsyncView>
    </>
  );
}
