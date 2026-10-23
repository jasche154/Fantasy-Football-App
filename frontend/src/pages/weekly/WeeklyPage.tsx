import { useState } from 'react';
import { refreshWeeklySources } from '../../api/actions';
import { errorMessage } from '../../api/client';
import { getWeeklyRankings } from '../../api/data';
import type { WeeklyRankingsResponse } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { PageHeader } from '../../components/Card';
import { Icon } from '../../components/Icon';
import { countLabel, LiveStatus } from '../../components/LiveStatus';
import { PositionFilter } from '../../components/PositionFilter';
import { SelectField } from '../../components/SelectField';
import { EmptyState, TableSkeleton } from '../../components/StatusViews';
import { useAction } from '../../hooks/useAction';
import { useAsync } from '../../hooks/useAsync';
import { useDocumentTitle } from '../../hooks/useDocumentTitle';
import { filterToPosition, positionFilterLabel, type PositionFilterValue } from '../../lib/positions';
import { describeSettings } from '../../state/leagueSettings';
import { useLeagueSettings } from '../../state/SettingsContext';
import { ScheduleStrengthView } from './ScheduleStrengthView';
import { RefreshNotes, SourceWeights } from './SourceWeights';
import { WeeklyTable } from './WeeklyTable';

const WEEKLY_LIMIT = 200;
const LAST_WEEK = 18;
const WEEK_OPTIONS = Array.from({ length: LAST_WEEK }, (_, index) => ({ value: index + 1, label: `Week ${index + 1}` }));
type View = 'rankings' | 'schedule';
const VIEWS: readonly { value: View; label: string }[] = [
  { value: 'rankings', label: 'Rankings' },
  { value: 'schedule', label: 'Schedule strength' },
];

function ViewToggle({ value, onChange }: { value: View; onChange: (view: View) => void }) {
  return (
    <div className="segmented" role="group" aria-label="View">
      {VIEWS.map((option) => (
        <button
          key={option.value}
          type="button"
          className={`segmented__option${option.value === value ? ' is-active' : ''}`}
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

function Legend({ sosTilt }: { sosTilt: number }) {
  return (
    <p className="legend">
      <span><strong>Proj</strong> = weighted blend of every source covering the player (weights above, re-balanced when one is missing).</span>
      <span><strong>Expert ranks</strong> become points: rank N gets the Nth best numeric projection at the position.</span>
      <span>
        <strong>Matchup</strong> = opponent-adjusted defense strength (#1 = easiest); it drives the model and adds a
        final tilt (strength<sup>{sosTilt}</sup>).
      </span>
    </p>
  );
}

function RefreshButton({ week, onRefreshed }: { week: number; onRefreshed: () => void }) {
  const refresh = useAction(refreshWeeklySources);
  const handleClick = async () => {
    if (await refresh.run(week)) onRefreshed();
  };
  return (
    <div className="refresh-action">
      <button
        type="button"
        className="btn btn--ghost btn--sm"
        title="Re-fetch Yahoo and ESPN now. Vegas props are bought once per game, near kickoff, to stay within the free credits."
        onClick={() => void handleClick()}
        disabled={refresh.status === 'pending'}
      >
        <Icon name="swap" size={14} /> {refresh.status === 'pending' ? 'Refreshing sources…' : 'Refresh sources'}
      </button>
      {refresh.status === 'error' && <p className="error-text" role="alert">{errorMessage(refresh.error)}</p>}
    </div>
  );
}

function weekContext(data: WeeklyRankingsResponse): string {
  return `${data.season} · week ${data.week}`;
}

export function WeeklyPage() {
  const { settings } = useLeagueSettings();
  const [filter, setFilter] = useState<PositionFilterValue>('ALL');
  const [week, setWeek] = useState<number | undefined>(undefined);
  const [view, setView] = useState<View>('rankings');
  const position = filterToPosition(filter);
  const rankings = useAsync(
    (signal) => getWeeklyRankings(settings, { week, position, limit: WEEKLY_LIMIT }, signal),
    [week, position, settings],
  );
  const shownWeek = week ?? rankings.data?.week;
  useDocumentTitle(shownWeek ? `Week ${shownWeek} Rankings` : 'Weekly Rankings');
  const label = positionFilterLabel(filter);

  return (
    <>
      <PageHeader
        title={shownWeek ? `Week ${shownWeek} Rankings` : 'Weekly Rankings'}
        subtitle={rankings.data ? `${weekContext(rankings.data)} · ${describeSettings(settings)}` : describeSettings(settings)}
        actions={
          <div className="weekly-controls">
            <SelectField label="Week" isInline value={shownWeek ?? ''}
              options={shownWeek ? WEEK_OPTIONS : [{ value: '', label: 'Current week' }, ...WEEK_OPTIONS]}
              onChange={(value) => setWeek(Number(value))} />
            <PositionFilter value={filter} onChange={setFilter} />
          </div>
        }
      />
      <ViewToggle value={view} onChange={setView} />
      {view === 'schedule' ? (
        <section aria-label="Schedule strength">
          {!position && <p className="field__hint">Showing WR. Pick a position above to rate defenses against it.</p>}
          <ScheduleStrengthView position={position ?? 'WR'} week={shownWeek} />
        </section>
      ) : (
        <section aria-label={`${label} week rankings`}>
          <LiveStatus state={rankings} describe={(data) => countLabel(data.results.length)} loadingText="Loading weekly rankings…" />
          <AsyncView
            state={rankings}
            loading={<TableSkeleton rows={12} columns={11} label="Loading weekly rankings…" isAnnounced={false} />}
            isEmpty={(data) => data.results.length === 0}
            empty={<EmptyState title="No projections for this week yet.">Players appear once their team has a game this week and a source covers them.</EmptyState>}
          >
            {(data) => (
              <>
                <div className="weekly-sources">
                  <SourceWeights weights={data.weights} />
                  <RefreshButton week={data.week} onRefreshed={rankings.reload} />
                </div>
                <RefreshNotes statuses={data.refresh} />
                <Legend sosTilt={data.sos_tilt} />
                <WeeklyTable rows={data.results} caption={`${label} projections for ${weekContext(data)}.`} />
              </>
            )}
          </AsyncView>
        </section>
      )}
    </>
  );
}
