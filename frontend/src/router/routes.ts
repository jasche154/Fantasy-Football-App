import { buildQuery, type QueryParams } from '../lib/query';

export type StaticRouteName =
  | 'dashboard'
  | 'team'
  | 'matchup'
  | 'rankings'
  | 'weekly'
  | 'charts'
  | 'waivers'
  | 'stats'
  | 'trade'
  | 'market'
  | 'ask'
  | 'settings';

export type Route =
  | { name: StaticRouteName }
  | { name: 'player'; id: number }
  | { name: 'notFound'; path: string };

export interface HashLocation {
  /** Normalised path, always starting with "/" and without a trailing slash. */
  path: string;
  query: Readonly<Record<string, string>>;
  route: Route;
}

const STATIC_ROUTES: Readonly<Record<string, StaticRouteName>> = {
  team: 'team',
  matchup: 'matchup',
  rankings: 'rankings',
  weekly: 'weekly',
  charts: 'charts',
  waivers: 'waivers',
  stats: 'stats',
  trade: 'trade',
  market: 'market',
  ask: 'ask',
  settings: 'settings',
};

export const paths = {
  dashboard: '/',
  team: '/team',
  matchup: '/matchup',
  rankings: '/rankings',
  weekly: '/weekly',
  charts: '/charts',
  waivers: '/waivers',
  stats: '/stats',
  trade: '/trade',
  market: '/market',
  ask: '/ask',
  settings: '/settings',
  player: (id: number): string => `/player/${id}`,
} as const;

/** Parses a strictly positive integer id ("12" -> 12, "1.5" / "-3" / "x" -> null). */
export function parsePositiveInt(value: string | undefined): number | null {
  if (!value || !/^\d+$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
}

function safeDecode(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

export function normalizePath(raw: string): string {
  const segments = raw.split('/').filter(Boolean).map(safeDecode);
  return `/${segments.join('/')}`;
}

export function matchRoute(path: string): Route {
  const segments = path.split('/').filter(Boolean);
  if (segments.length === 0) return { name: 'dashboard' };
  const [head, ...rest] = segments;
  if (head === 'player' && rest.length === 1) {
    const id = parsePositiveInt(rest[0]);
    if (id !== null) return { name: 'player', id };
  }
  const staticName = STATIC_ROUTES[head];
  if (staticName && rest.length === 0) return { name: staticName };
  return { name: 'notFound', path };
}

/** "#/trade?give=1,2&get=3" -> { path: "/trade", query: {give: "1,2", get: "3"}, route } */
export function parseHash(hash: string): HashLocation {
  const raw = hash.startsWith('#') ? hash.slice(1) : hash;
  const queryIndex = raw.indexOf('?');
  const pathPart = queryIndex >= 0 ? raw.slice(0, queryIndex) : raw;
  const queryPart = queryIndex >= 0 ? raw.slice(queryIndex + 1) : '';
  const path = normalizePath(pathPart);
  const query = Object.freeze(Object.fromEntries(new URLSearchParams(queryPart)));
  return { path, query, route: matchRoute(path) };
}

/** Keeps commas readable in shareable hashes ("give=1,2" rather than "give=1%2C2"). */
function encodeHashValue(value: string): string {
  return encodeURIComponent(value).replace(/%2C/gi, ',');
}

export function buildHash(path: string, query: QueryParams = {}): string {
  return `#${normalizePath(path)}${buildQuery(query, encodeHashValue)}`;
}
