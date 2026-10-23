import { useState } from 'react';
import { getWaivers } from '../../api/data';
import type { WaiverCandidate } from '../../api/types';
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

const ROSTERED_OPTIONS = [10, 25, 50, 75].map((v) => ({ value: v, label: `Under ${v}% rostered` }));

function WaiverRow({ row }: { row: WaiverCandidate }) {
  return (
    <li className="waiver">
      <div className="waiver__head">
        <PlayerLink player={row.player} />
        <StatusBadge status={row.status} playProb={row.play_prob} />
        <span className="waiver__rostered">{formatNumber(row.pct_rostered, 0)}% rostered</span>
        <span className="waiver__bid" title="Suggested share of your FAAB budget">
          {row.faab_label} · bid {row.faab_pct}%
        </span>
      </div>
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
  const waivers = useAsync((signal) => getWaivers(settings, { position, maxRostered, limit: 30 }, signal),
    [position, maxRostered, settings]);
  return (
    <>
      <PageHeader
        title="Waiver Wire"
        subtitle={`Adds ranked by rest-of-season value, this week, next 3 matchups and injury-driven opportunity · ${describeSettings(settings)}`}
        actions={
          <div className="weekly-controls">
            <SelectField label="Rostered" isInline value={maxRostered} options={ROSTERED_OPTIONS}
              onChange={(value) => setMaxRostered(Number(value))} />
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
