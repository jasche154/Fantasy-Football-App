/** Pure helpers for the player card's Advanced tab: rates, season averages and a last-3 trend, from the weeks the API already sends. */
import type { ReplayBoxLine, ReplayPlayerWeek } from '../../api/types';

/** The trend compares his last this-many games with his whole season. */
export const RECENT_GAMES = 3;

export type Direction = 'up' | 'down' | 'flat';

export interface Trend {
  recent: number;
  season: number;
  direction: Direction;
}

export interface SeasonSummary {
  games: number;
  /** 0-100. */
  snapPct: number | null;
  /** 0-1. */
  targetShare: number | null;
  targetsPerGame: number | null;
  carriesPerGame: number | null;
  /** 0-1. */
  catchPct: number | null;
  ydsPerTarget: number | null;
  ydsPerCarry: number | null;
  /** 0-1; null without a recorded attempt. */
  cmpPct: number | null;
}

/** num / den, or null when there is nothing to divide by (no targets, no carries). */
export function rate(num: number, den: number): number | null {
  return den > 0 ? num / den : null;
}

/** The mean of the numbers present; null when there are none. */
export function mean(values: readonly (number | null)[]): number | null {
  const present = values.filter((value): value is number => value !== null);
  return rate(present.reduce((sum, value) => sum + value, 0), present.length);
}

/** Weeks he took the field: usage is null on byes, unplayed weeks and games he missed. */
export function gamesPlayed(weeks: readonly ReplayPlayerWeek[]): ReplayPlayerWeek[] {
  return weeks.filter((week) => week.played && week.usage !== null);
}

const ZERO_BOX: ReplayBoxLine = { pass_cmp: 0, pass_att: 0, pass_yds: 0, pass_td: 0, pass_int: 0, carries: 0, rush_yds: 0, rush_td: 0, targets: 0, rec: 0, rec_yds: 0, rec_td: 0 };

/** Box lines added up stat by stat (completions and attempts never recorded count as none). */
export function boxTotals(boxes: readonly ReplayBoxLine[]): ReplayBoxLine {
  return boxes.reduce((sum, box) => {
    const next = { ...sum };
    for (const key of Object.keys(ZERO_BOX) as (keyof ReplayBoxLine)[]) next[key] = (sum[key] ?? 0) + (box[key] ?? 0);
    return next;
  }, ZERO_BOX);
}

/** Season averages over the games he played: snaps and share from usage, counting stats from the box score. */
export function seasonSummary(weeks: readonly ReplayPlayerWeek[]): SeasonSummary {
  const games = gamesPlayed(weeks);
  const boxes = games.flatMap((week) => (week.box ? [week.box] : []));
  const total = boxTotals(boxes);
  return {
    games: games.length,
    snapPct: mean(games.map((week) => week.usage?.snap_pct ?? null)),
    targetShare: mean(games.map((week) => week.usage?.target_share ?? null)),
    targetsPerGame: rate(total.targets, boxes.length),
    carriesPerGame: rate(total.carries, boxes.length),
    catchPct: rate(total.rec, total.targets),
    ydsPerTarget: rate(total.rec_yds, total.targets),
    ydsPerCarry: rate(total.rush_yds, total.carries),
    cmpPct: rate(total.pass_cmp ?? 0, total.pass_att ?? 0),
  };
}

/**
 * Last RECENT_GAMES games against the season (values in week order, nulls skipped). Null until he has
 * played more games than that, since before then the two are the same games. Within `flatWithin` is flat.
 */
export function trend(values: readonly (number | null)[], flatWithin: number): Trend | null {
  const present = values.filter((value): value is number => value !== null);
  if (present.length <= RECENT_GAMES) return null;
  const recent = mean(present.slice(-RECENT_GAMES)) as number;
  const season = mean(present) as number;
  const gap = recent - season;
  const direction: Direction = Math.abs(gap) < flatWithin ? 'flat' : gap > 0 ? 'up' : 'down';
  return { recent, season, direction };
}
