import type { LeagueSettings } from '../api/types';

export type QueryValue = string | number | boolean | null | undefined;
export type QueryParams = Readonly<Record<string, QueryValue>>;

type PresentValue = string | number | boolean;

function isPresent(value: QueryValue): value is PresentValue {
  if (value === null || value === undefined) return false;
  if (typeof value === 'string') return value.length > 0;
  if (typeof value === 'number') return Number.isFinite(value);
  return true;
}

/**
 * Serialises params into a query string ("?a=1&b=two"), skipping null, undefined,
 * empty strings and non-finite numbers. Returns "" when nothing is left.
 */
export function buildQuery(
  params: QueryParams,
  encode: (value: string) => string = encodeURIComponent,
): string {
  const pairs = Object.entries(params)
    .filter((entry): entry is [string, PresentValue] => isPresent(entry[1]))
    .map(([key, value]) => `${encodeURIComponent(key)}=${encode(String(value))}`);
  return pairs.length > 0 ? `?${pairs.join('&')}` : '';
}

/** League settings as the backend's shared query params (scoring, teams, superflex, te_premium). */
export function settingsToParams(settings: LeagueSettings): QueryParams {
  return {
    scoring: settings.scoring,
    teams: settings.teams,
    superflex: settings.superflex,
    te_premium: settings.te_premium,
  };
}
