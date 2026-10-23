import { useId } from 'react';
import type { MetricCategory } from '../../api/types';
import { SelectField } from '../../components/SelectField';
import { POSITION_FILTERS, positionFilterLabel, type PositionFilterValue } from '../../lib/positions';
import {
  LEADERBOARD_LIMITS,
  parseMinVolume,
  type LeaderboardSelection,
  type ResolvedMetric,
} from './leaderboardSelection';

const POSITION_OPTIONS = POSITION_FILTERS.map((filter) => ({ value: filter, label: positionFilterLabel(filter) }));
const LIMIT_OPTIONS = LEADERBOARD_LIMITS.map((limit) => ({ value: limit, label: `Top ${limit}` }));

interface MinVolumeFieldProps {
  volumeLabel: string;
  value: string;
  onChange: (value: string) => void;
}

function MinVolumeField({ volumeLabel, value, onChange }: MinVolumeFieldProps) {
  const id = useId();
  const parsed = parseMinVolume(value);
  return (
    <div className="field">
      <label className="field__label" htmlFor={id}>Min {volumeLabel.toLowerCase()}</label>
      <input
        id={id}
        className="input"
        type="number"
        inputMode="numeric"
        min={0}
        step={1}
        placeholder="Default"
        value={value}
        aria-invalid={!parsed.ok}
        aria-describedby={parsed.ok ? undefined : `${id}-error`}
        onChange={(event) => onChange(event.target.value)}
      />
      {!parsed.ok && <p id={`${id}-error`} className="field__error">{parsed.message}</p>}
    </div>
  );
}

interface LeaderboardControlsProps {
  categories: readonly MetricCategory[];
  resolved: ResolvedMetric;
  selection: LeaderboardSelection;
  seasons: readonly number[];
  displaySeason: number | undefined;
  onCategory: (key: string) => void;
  onMetric: (key: string) => void;
  onChange: (patch: Partial<LeaderboardSelection>) => void;
}

export function LeaderboardControls(props: LeaderboardControlsProps) {
  const { categories, resolved, selection, seasons, displaySeason, onCategory, onMetric, onChange } = props;
  const seasonOptions = seasons.length > 0
    ? seasons.map((season) => ({ value: season, label: String(season) }))
    : [{ value: '', label: 'Current' }];
  return (
    <form className="controls" onSubmit={(event) => event.preventDefault()} aria-label="Leaderboard options">
      <SelectField
        label="Category"
        value={resolved.category.key}
        options={categories.map((category) => ({ value: category.key, label: category.label }))}
        onChange={onCategory}
      />
      <SelectField
        label="Metric"
        className="field--wide"
        value={resolved.metric.key}
        options={resolved.category.metrics.map((metric) => ({ value: metric.key, label: metric.label }))}
        onChange={onMetric}
      />
      <SelectField
        label="Season"
        value={displaySeason ?? ''}
        options={seasonOptions}
        disabled={seasons.length === 0}
        onChange={(value) => onChange({ season: Number(value) })}
      />
      <SelectField
        label="Position"
        value={selection.position}
        options={POSITION_OPTIONS}
        onChange={(value) => onChange({ position: value as PositionFilterValue })}
      />
      <MinVolumeField volumeLabel={resolved.metric.volume_label} value={selection.minVolume} onChange={(minVolume) => onChange({ minVolume })} />
      <SelectField label="Show" value={selection.limit} options={LIMIT_OPTIONS} onChange={(value) => onChange({ limit: Number(value) })} />
      <label className="checkbox field--inline">
        <input type="checkbox" checked={selection.ascending} onChange={(event) => onChange({ ascending: event.target.checked })} />
        <span>Ascending (lowest first)</span>
      </label>
    </form>
  );
}
