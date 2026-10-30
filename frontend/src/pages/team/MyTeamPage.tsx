import { useState } from 'react';
import { getLeagueOverview, getLeagueTrades, getLeagueWaivers, getMyTeam, getRosterNews, syncMyLeague } from '../../api/data';
import type { LeagueTradesResponse, ManagerTendencies, MyLeague, MyTeamResponse, MyTeamRosterRow, Position } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { PageHeader } from '../../components/Card';
import { FlagChips, StatusBadge } from '../../components/FlagChips';
import { PlayerLink } from '../../components/PlayerLink';
import { PositionFilter } from '../../components/PositionFilter';
import { SelectField } from '../../components/SelectField';
import { EmptyState, TableSkeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { useDocumentTitle } from '../../hooks/useDocumentTitle';
import { EMPTY_VALUE, formatNumber } from '../../lib/format';
import { filterToPosition, type PositionFilterValue } from '../../lib/positions';
import { paths, buildHash } from '../../router/routes';
import { useMyLeague } from '../../state/useMyLeague';
import { WaiverRow } from '../waivers/WaiversPage';
import { NewsList } from '../../components/NewsList';
import { LeagueView, TendencyNote } from './LeagueView';
import { PlayoffsView } from './PlayoffsView';

type View = 'lineup' | 'waivers' | 'trades' | 'playoffs' | 'league';
const VIEWS: readonly { value: View; label: string }[] = [
  { value: 'lineup', label: 'Lineup' },
  { value: 'waivers', label: 'Waivers' },
  { value: 'trades', label: 'Trades' },
  { value: 'playoffs', label: 'Playoffs' },
  { value: 'league', label: 'League' },
];
const SCORING_LABELS: Record<string, string> = { ppr: 'PPR', half: 'Half PPR', std: 'Standard' };

function ViewToggle({ value, onChange }: { value: View; onChange: (view: View) => void }) {
  return (
    <div className="segmented" role="group" aria-label="View">
      {VIEWS.map((option) => (
        <button key={option.value} type="button" aria-pressed={option.value === value}
          className={`segmented__option${option.value === value ? ' is-active' : ''}`} onClick={() => onChange(option.value)}>
          {option.label}
        </button>
      ))}
    </div>
  );
}

function RosterTable({ rows, caption }: { rows: readonly MyTeamRosterRow[]; caption: string }) {
  return (
    <div className="table-wrap">
      <table className="data-table">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Slot</th>
            <th scope="col">Player</th>
            <th scope="col">Matchup</th>
            <th scope="col" className="num" title="This week's projection, with the 10th-90th percentile range">Proj</th>
            <th scope="col" className="num" title="Points scored, once his game is final">Actual</th>
            <th scope="col" className="num" title="Rest-of-season points per game">ROS PPG</th>
            <th scope="col" title="Rest-of-season positional rank">ROS rank</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const week = row.week;
            const shouldStart = !row.starting && row.in_best_lineup;
            const shouldSit = row.starting && !row.in_best_lineup;
            return (
              <tr key={row.player.id} className={shouldStart ? 'row--promote' : shouldSit ? 'row--demote' : undefined}>
                <td className="td--pos">{row.slot}</td>
                <td className="td--player">
                  <PlayerLink player={row.player} />
                  {week && <StatusBadge status={week.status} playProb={week.play_prob} />}
                  {shouldStart && <span className="status-badge status-badge--ok">Start</span>}
                  {shouldSit && <span className="status-badge status-badge--out">Sit</span>}
                  {week && <FlagChips flags={week.flags} />}
                </td>
                <td>{week ? `${week.matchup.home ? 'vs' : '@'} ${week.matchup.opponent}` : 'No game / out'}</td>
                <td className="num">
                  {week ? <span className="value-pill">{formatNumber(week.projection)}</span> : EMPTY_VALUE}
                  {week && week.floor !== null && week.ceiling !== null && (
                    <span className="source-rank">{formatNumber(week.floor)}–{formatNumber(week.ceiling)}</span>
                  )}
                </td>
                <td className="num">{week?.actual != null ? formatNumber(week.actual) : EMPTY_VALUE}</td>
                <td className="num">{formatNumber(row.ros_ppg)}</td>
                <td>{row.pos_rank ?? EMPTY_VALUE}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function LineupView({ data }: { data: MyTeamResponse }) {
  const { lineup } = data;
  const starters = data.roster.filter((r) => r.starting);
  const bench = data.roster.filter((r) => !r.starting);
  return (
    <>
      <section className="lineup-advice" aria-label="Lineup advice">
        {lineup.changes.length === 0 ? (
          <p className="lineup-advice__ok">Your lineup is already the best one this week ({formatNumber(lineup.current_total)} projected).</p>
        ) : (
          <>
            <p className="lineup-advice__summary">
              Best lineup projects <strong>{formatNumber(lineup.best_total)}</strong>, against{' '}
              {formatNumber(lineup.current_total)} for your current one.
            </p>
            <ul className="lineup-advice__list">
              {lineup.changes.map((change) => (
                <li key={change.start.id}>
                  Start <strong>{change.start.name}</strong> ({formatNumber(change.start_projection)}) at {change.slot}
                  {change.bench ? <> over <strong>{change.bench.name}</strong> ({formatNumber(change.bench_projection ?? 0)})</> : ' in the empty slot'}
                  {' '}<span className="lineup-advice__gain">+{formatNumber(change.gain)}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>
      <h2 className="section-title">Starters</h2>
      <RosterTable rows={starters} caption="Starters" />
      <h2 className="section-title">Bench and IR</h2>
      <RosterTable rows={bench} caption="Bench and injured reserve" />
      <p className="hint">Kickers and team defenses aren't modeled, so they're not shown.</p>
      <RosterNews leagueId={data.league.id} />
    </>
  );
}

function RosterNews({ leagueId }: { leagueId: number }) {
  const news = useAsync((signal) => getRosterNews(leagueId, signal), [leagueId]);
  return (
    <section aria-label="Roster news">
      <h2 className="section-title">Latest news</h2>
      <AsyncView state={news} loading={<TableSkeleton rows={3} columns={1} label="Loading news…" />}
        isEmpty={(d) => d.items.length === 0} empty={<EmptyState title="No recent news for your players." />}
        errorTitle="News is unavailable right now">
        {(d) => <><NewsList items={d.items} showPlayer /><p className="hint attribution">News via Sleeper.</p></>}
      </AsyncView>
    </section>
  );
}

function WaiversView({ league }: { league: MyLeague }) {
  const [filter, setFilter] = useState<PositionFilterValue>('ALL');
  const position = filterToPosition(filter);
  const waivers = useAsync((signal) => getLeagueWaivers(league.id, { position, limit: 20 }, signal), [league.id, position]);
  return (
    <>
      <div className="weekly-controls"><PositionFilter value={filter} onChange={setFilter} /></div>
      <AsyncView state={waivers} loading={<TableSkeleton rows={6} columns={3} label="Finding free agents…" />}
        isEmpty={(d) => d.results.length === 0}
        empty={<EmptyState title="No free agents worth adding right now." />}>
        {(data) => (
          <ol className="waiver-list" aria-label={`Free agents in ${data.league}`}>
            {data.results.map((row) => <WaiverRow key={row.player.id} row={row} />)}
          </ol>
        )}
      </AsyncView>
    </>
  );
}

function TradeTeam({ team, habits }: { team: LeagueTradesResponse['teams'][number]; habits?: ManagerTendencies | null }) {
  return (
    <li className="league-trade">
      <div className="league-trade__head">
        <strong>{team.team}</strong>
        {team.owner && <span className="league-trade__owner">{team.owner}</span>}
        {team.needs.length > 0 && <span className="league-trade__needs">Needs {team.needs.join(', ')}</span>}
        {habits && <TendencyNote habits={habits} />}
      </div>
      <ul className="league-trade__offers">
        {team.offers.map((offer) => (
          <li key={`${offer.give.map((p) => p.id).join('-')}-${offer.get.id}`}>
            Give {offer.give.map((p) => p.name).join(' + ')} for <strong>{offer.get.name}</strong> ({offer.get.position})
            {' '}· {offer.evaluation.verdict.replace('_', ' ')}
            {offer.fills_their_need.length > 0 && <>{' '}<span className="status-badge status-badge--ok">Fills their {offer.fills_their_need.join('/')} need</span></>}
            {' '}<a href={buildHash(paths.trade, { give: offer.give.map((p) => p.id).join(','), get: String(offer.get.id) })}>Open in calculator</a>
          </li>
        ))}
      </ul>
    </li>
  );
}

function TradesView({ league }: { league: MyLeague }) {
  const [filter, setFilter] = useState<PositionFilterValue>('ALL');
  const position = filterToPosition(filter) as Position | undefined;
  const trades = useAsync((signal) => getLeagueTrades(league.id, position, signal), [league.id, position]);
  // Best-effort: tendencies come from Yahoo; without them the offers still show.
  const overview = useAsync((signal) => getLeagueOverview(league.id, signal).catch(() => null), [league.id]);
  const habitsByTeam = new Map((overview.data?.teams ?? []).map((t) => [t.team, t.tendencies]));
  return (
    <>
      <div className="weekly-controls"><PositionFilter value={filter} onChange={setFilter} /></div>
      <AsyncView state={trades} loading={<TableSkeleton rows={6} columns={3} label="Looking at every roster…" />}
        isEmpty={(d) => d.teams.length === 0}
        empty={<EmptyState title="No fair trades found.">Try another position.</EmptyState>}>
        {(data) => (
          <>
            <p className="hint">Offers are fair by this app's values and by expert consensus. Teams that need what you'd send come first.</p>
            <ol className="league-trades" aria-label={`Trade ideas in ${data.league}`}>
              {data.teams.map((team) => <TradeTeam key={team.team ?? ''} team={team} habits={habitsByTeam.get(team.team ?? '')} />)}
            </ol>
          </>
        )}
      </AsyncView>
    </>
  );
}

export function MyTeamPage() {
  useDocumentTitle('My Team');
  const { leagues, league, choose } = useMyLeague();
  const [view, setView] = useState<View>('lineup');
  const [reload, setReload] = useState(0);
  const [syncing, setSyncing] = useState<string | null>(null);
  const team = useAsync((signal) => (league ? getMyTeam(league.id, signal) : Promise.resolve(null)), [league?.id, reload]);

  const sync = async () => {
    if (!league) return;
    setSyncing('Syncing from Yahoo…');
    try {
      const result = await syncMyLeague(league.id);
      setSyncing(`Synced ${result.roster_spots} roster spots from Yahoo.`);
      setReload((n) => n + 1);
    } catch {
      setSyncing("Couldn't reach Yahoo. Check the connection in Settings.");
    }
  };

  const options = (leagues.data?.leagues ?? []).map((l) => ({ value: l.id, label: `${l.name} · ${l.team ?? ''}` }));
  return (
    <>
      <PageHeader
        title={league?.team ?? 'My Team'}
        subtitle={league ? `${league.name} · ${league.teams ?? ''} teams · ${SCORING_LABELS[league.scoring] ?? league.scoring}` : 'Your imported Yahoo team'}
        actions={league && (
          <div className="weekly-controls">
            {options.length > 1 && <SelectField label="League" isInline value={league.id} options={options} onChange={(v) => choose(Number(v))} />}
            <ViewToggle value={view} onChange={setView} />
            <button type="button" className="btn btn--ghost btn--sm" onClick={sync} disabled={syncing === 'Syncing from Yahoo…'}>Sync from Yahoo</button>
          </div>
        )}
      />
      {syncing && <p className="hint" role="status">{syncing}</p>}
      <AsyncView state={leagues} loading={<TableSkeleton rows={4} columns={3} label="Loading your leagues…" />}
        isEmpty={(d) => d.leagues.length === 0}
        empty={<EmptyState title="No Yahoo league imported yet.">Connect Yahoo and import a league in <a href={buildHash(paths.settings)}>Settings</a>.</EmptyState>}>
        {() => league && (
          view === 'lineup' ? (
            <AsyncView state={team} loading={<TableSkeleton rows={10} columns={6} label="Loading your roster…" />}
              isEmpty={(d) => !d || d.roster.length === 0} empty={<EmptyState title="Roster not imported yet." />}>
              {(data) => data && <LineupView data={data} />}
            </AsyncView>
          ) : view === 'waivers' ? <WaiversView league={league} /> : view === 'playoffs' ? <PlayoffsView league={league} />
            : view === 'league' ? <LeagueView league={league} /> : <TradesView league={league} />
        )}
      </AsyncView>
      {league && <p className="hint attribution">Fantasy data provided by Yahoo Fantasy.</p>}
    </>
  );
}
