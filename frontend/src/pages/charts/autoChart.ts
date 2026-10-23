import type { GameLogRow, MetricCategory, MetricDef, PlayerSummary, Position } from '../../api/types';

export type WeeklyStatKey = Exclude<keyof GameLogRow, 'week' | 'season_type' | 'opponent'>;

export const WEEKLY_STATS: readonly { key: WeeklyStatKey; label: string }[] = [
  { key: 'fantasy_points', label: 'Fantasy points' },
  { key: 'expected_points', label: 'Expected fantasy points' },
  { key: 'targets', label: 'Targets' },
  { key: 'receptions', label: 'Receptions' },
  { key: 'receiving_yards', label: 'Receiving yards' },
  { key: 'carries', label: 'Carries' },
  { key: 'rushing_yards', label: 'Rushing yards' },
  { key: 'passing_yards', label: 'Passing yards' },
  { key: 'snap_pct', label: 'Snap share (%)' },
];

/** "usage:target_share" identifies one leaderboard metric. */
export type MetricId = `${string}:${string}`;

export interface ChartSelection {
  players: readonly PlayerSummary[];
  weeklyStat: WeeklyStatKey;
  metrics: readonly MetricId[];
  position: Position | null;
}

export type ChartKind = 'trend' | 'leaders' | 'scatter';

export interface ChartPlan {
  kind: ChartKind;
  /** Why this chart type was chosen, shown next to the chart. */
  reason: string;
}

export const MAX_TREND_PLAYERS = 5;

/**
 * Picks the chart that best answers the selection:
 * two season metrics → scatter (how they relate), one → ranked leaders,
 * players with no season metric → their weekly trend.
 */
export function chooseChart(selection: ChartSelection): ChartPlan | null {
  const metricCount = selection.metrics.length;
  if (metricCount >= 2) {
    return { kind: 'scatter', reason: 'Two season metrics: a scatter shows how they relate, split at the medians.' };
  }
  if (metricCount === 1) {
    return { kind: 'leaders', reason: 'One season metric: a ranked bar chart shows the leaders.' };
  }
  if (selection.players.length > 0) {
    return { kind: 'trend', reason: 'Players without a season metric: a line chart shows their week-by-week trend.' };
  }
  return null;
}

export function parseMetricId(id: MetricId): { category: string; metric: string } {
  const [category, metric] = id.split(':');
  return { category, metric };
}

export function findMetric(catalog: readonly MetricCategory[], id: MetricId): MetricDef | undefined {
  const { category, metric } = parseMetricId(id);
  return catalog.find((c) => c.key === category)?.metrics.find((m) => m.key === metric);
}

export interface ChartPreset {
  name: string;
  blurb: string;
  metrics: readonly MetricId[];
  position: Position | null;
}

/** One-click charts that answer common fantasy questions. */
export const PRESETS: readonly ChartPreset[] = [
  { name: 'Volume pays', blurb: 'Target share vs. PPR points: who earns looks, and who cashes them in.',
    metrics: ['usage:target_share', 'usage:ppr_points_per_game'], position: 'WR' },
  { name: 'Separators', blurb: 'Separation vs. YAC over expected for receivers.',
    metrics: ['receiving:avg_separation', 'receiving:avg_yac_above_expectation'], position: 'WR' },
  { name: 'RYOE leaders', blurb: 'Rush yards over expected per attempt: the best runners regardless of blocking.',
    metrics: ['rushing:ryoe_per_att'], position: 'RB' },
  { name: 'Downfield QBs', blurb: 'Aggressiveness vs. completion % over expected.',
    metrics: ['passing:aggressiveness', 'passing:cpoe'], position: 'QB' },
  { name: 'Opportunity score', blurb: 'WOPR (target + air-yard share) leaders at tight end.',
    metrics: ['usage:wopr'], position: 'TE' },
];
