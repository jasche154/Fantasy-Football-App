import { useState } from 'react';
import { getLeagueWaivers, getWaivers } from '../../api/data';
import type { LeagueWaiverCandidate, WaiverCandidate } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { PageHeader } from '../../components/Card';
import { FlagChips, StatusBadge } from '../../components/FlagChips';
import { PlayerLink } from '../../components/PlayerLink';
import { PositionFilter } from '../../components/PositionFilter';
import { SelectField } from '../../components/SelectField';
import { EmptyState, TableSkeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { useDocumentTitle } from '../../hooks/useDocumentTitle';
import { formatNumber } from '../../lib/format';
import { filterToPosition, type PositionFilterValue } from '../../lib/positions';
import { describeSettings } from '../../state/leagueSettings';
import { useLeagueSettings } from '../../state/SettingsContext';
import { useMyLeague } from '../../state/useMyLeague';

const ROSTERED_OPTIONS = [10, 25, 50, 75].map((v) => ({ value: v, label: `Under ${v}% rostered` }));

const ACTION_LABELS: Record<string, string> = { bid: 'Bid', pass: 'Pass', claim: 'Claim', wait: 'Wait', add: 'Add' };

export function WaiverRow({ row }: { row: WaiverCandidate | LeagueWaiverCandidate }) {
  const league = 'advice' in row && row.advice ? row : null;
  return (
    <li className="waiver">
      <div className="waiver__head">
        <PlayerLink player={row.player} />
        <StatusBadge status={row.status} playProb={row.play_prob} />
        <span className="waiver__rostered">{formatNumber(row.pct_rostered, 0)}% rostered</span>
        {league?.advice ? (
          <span className={`waiver__action waiver__action--${league.advice.action}`}>
            {ACTION_LABELS[league.advice.action] ?? league.advice.action}
            {league.advice.bid != null && ` $${league.advice.bid}`}
          </span>
        ) : (
          <span className="waiver__bid" title="Suggested share of your FAAB budget">
            {row.faab_label} · bid {row.faab_pct}%
          </span>
        )}
      </div>
      {league?.advice && (
        <p className="waiver__advice">
          <strong>+{formatNumber(league.lineup_gain ?? 0)} lineup points</strong> rest of season
          {league.drop && <> · drop <PlayerLink player={league.drop} /></>}
          {' · '}{league.advice.note}
        </p>
      )}
      <p className="waiver__numbers">
        This week {formatNumber(row.projection)} ({formatNumber(row.floor)}–{formatNumber(row.ceiling)}) · ROS value{' '}
        {formatNumber(row.ros_value, 0)} · next 3 weeks {row.schedule_factor >= 1 ? '+' : ''}
        {formatNumber((row.schedule_factor - 1) * 100, 0)}% schedule
      </p>
      <FlagChips flags={row.flags} />
      <ul className="reason-list">{row.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
    </li>
  );
}

export function WaiversPage() {
  useDocumentTitle('Waiver Wire');
  const { settings } = useLeagueSettings();
  const [filter, setFilter] = useState<PositionFilterValue>('ALL');
  const [maxRostered, setMaxRostered] = useState(50);
  const position = filterToPosition(filter);
  const { leagues, league, choose } = useMyLeague();
  // With a Yahoo league imported, show who's actually free in it; "espn" falls back to ESPN roster rates.
  const [scope, setScope] = useState<'league' | 'espn'>('league');
  const leagueId = scope === 'league' ? league?.id ?? null : null;
  const waivers = useAsync((signal) => (leagueId !== null
    ? getLeagueWaivers(leagueId, { position, limit: 30 }, signal).then((d) => ({ ...d, max_rostered: 100 }))
    : getWaivers(settings, { position, maxRostered, limit: 30 }, signal)),
  [position, maxRostered, settings, leagueId]);
  const scopeOptions = [
    ...(leagues.data?.leagues ?? []).map((l) => ({ value: `league:${l.id}`, label: `Free in ${l.name}` })),
    { value: 'espn', label: 'ESPN roster rates' },
  ];
  const scopeValue = leagueId !== null ? `league:${leagueId}` : 'espn';
  return (
    <>
      <PageHeader
        title="Waiver Wire"
        subtitle={`Adds ranked by rest-of-season value, this week, next 3 matchups and injury-driven opportunity · ${describeSettings(settings)}`}
        actions={
          <div className="weekly-controls">
            {scopeOptions.length > 1 && (
              <SelectField label="Show" isInline value={scopeValue} options={scopeOptions}
                onChange={(value) => {
                  if (value === 'espn') setScope('espn');
                  else { setScope('league'); choose(Number(String(value).split(':')[1])); }
                }} />
            )}
            {leagueId === null && (
              <SelectField label="Rostered" isInline value={maxRostered} options={ROSTERED_OPTIONS}
                onChange={(value) => setMaxRostered(Number(value))} />
            )}
            <PositionFilter value={filter} onChange={setFilter} />
          </div>
        }
      />
      <AsyncView state={waivers} loading={<TableSkeleton rows={8} columns={4} label="Finding adds…" />}
        isEmpty={(d) => d.results.length === 0}
        empty={<EmptyState title="No waiver targets yet.">Roster rates come from ESPN; refresh the Week X page first.</EmptyState>}>
        {(data) => <ol className="waiver-list" aria-label={`Week ${data.week} waiver targets`}>{data.results.map((row) => <WaiverRow key={row.player.id} row={row} />)}</ol>}
      </AsyncView>
    </>
  );
}
