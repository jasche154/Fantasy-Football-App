import type { ReplayStatsSpan, ReplaySummary } from '../../api/types';
import { SelectField } from '../../components/SelectField';

export interface StatsChoice {
  span: ReplayStatsSpan;
  /** Set for span "week" (1..current). */
  week: number | null;
}

export const SEASON_STATS: StatsChoice = { span: 'season', week: null };

function encode({ span, week }: StatsChoice): string {
  return span === 'week' ? `week:${week}` : span;
}

function decode(value: string): StatsChoice {
  if (value.startsWith('week:')) return { span: 'week', week: Number(value.slice(5)) };
  return { span: value as ReplayStatsSpan, week: null };
}

/** The current week is unplayed until the season is over: it has a projection, no score yet. */
export function isUnplayed(game: ReplaySummary, week: number): boolean {
  return week === game.week && game.phase !== 'complete';
}

interface StatsPickerProps {
  game: ReplaySummary;
  value: StatsChoice;
  onChange: (value: StatsChoice) => void;
}

/** Which numbers the table shows: season totals, the last 4 games' average, or one week (1..current). */
export function StatsPicker({ game, value, onChange }: StatsPickerProps) {
  const weeks = Array.from({ length: Math.max(0, game.week) }, (_, index) => index + 1);
  const options = [
    { value: 'season', label: 'Season totals' },
    { value: 'last4', label: 'Last 4 games (avg)' },
    ...weeks.map((week) => ({ value: `week:${week}`, label: isUnplayed(game, week) ? `Week ${week} (proj)` : `Week ${week}` })),
  ];
  return <SelectField label="Stats" isInline value={encode(value)} options={options} onChange={(next) => onChange(decode(next))} />;
}
