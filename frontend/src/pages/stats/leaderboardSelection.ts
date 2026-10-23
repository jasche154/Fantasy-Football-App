import type { MetricCategory, MetricDef } from '../../api/types';
import type { PositionFilterValue } from '../../lib/positions';

export const LEADERBOARD_LIMITS: readonly number[] = [25, 50, 100];

export interface LeaderboardSelection {
  categoryKey: string;
  metricKey: string;
  /** undefined = backend default (current season). */
  season: number | undefined;
  position: PositionFilterValue;
  /** Raw text of the min-volume input; empty = per-metric default. */
  minVolume: string;
  ascending: boolean;
  limit: number;
}

export const INITIAL_SELECTION: LeaderboardSelection = {
  categoryKey: '',
  metricKey: '',
  season: undefined,
  position: 'ALL',
  minVolume: '',
  ascending: false,
  limit: LEADERBOARD_LIMITS[0],
};

export interface ResolvedMetric {
  category: MetricCategory;
  metric: MetricDef;
}

/** Finds the selected category/metric, falling back to the first available ones. */
export function resolveMetric(
  categories: readonly MetricCategory[],
  selection: Pick<LeaderboardSelection, 'categoryKey' | 'metricKey'>,
): ResolvedMetric | null {
  const usable = categories.filter((category) => category.metrics.length > 0);
  const category = usable.find((item) => item.key === selection.categoryKey) ?? usable[0];
  if (!category) return null;
  const metric = category.metrics.find((item) => item.key === selection.metricKey) ?? category.metrics[0];
  return { category, metric };
}

/** Lower-is-better metrics (e.g. interception rate) default to ascending order. */
export function withCategory(
  selection: LeaderboardSelection,
  categories: readonly MetricCategory[],
  categoryKey: string,
): LeaderboardSelection {
  const resolved = resolveMetric(categories, { categoryKey, metricKey: '' });
  if (!resolved) return selection;
  return {
    ...selection,
    categoryKey: resolved.category.key,
    metricKey: resolved.metric.key,
    ascending: !resolved.metric.higher_is_better,
  };
}

export function withMetric(
  selection: LeaderboardSelection,
  categories: readonly MetricCategory[],
  metricKey: string,
): LeaderboardSelection {
  const resolved = resolveMetric(categories, { categoryKey: selection.categoryKey, metricKey });
  if (!resolved) return selection;
  return {
    ...selection,
    categoryKey: resolved.category.key,
    metricKey: resolved.metric.key,
    ascending: !resolved.metric.higher_is_better,
  };
}

export type MinVolumeParse = { ok: true; value: number | undefined } | { ok: false; message: string };

export function parseMinVolume(raw: string): MinVolumeParse {
  const value = raw.trim();
  if (!value) return { ok: true, value: undefined };
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < 0) {
    return { ok: false, message: 'Enter a number of 0 or more, or leave blank for the default.' };
  }
  return { ok: true, value: parsed };
}
