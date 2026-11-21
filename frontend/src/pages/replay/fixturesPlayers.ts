/** Players-screen and player-card fixtures (box lines, defense ratings, upcoming weeks) on top of fixtures.ts. */
import type { ReplayBoxLine, ReplayDefenseRating, ReplayPlayerDetail, ReplayPlayerRef, ReplayUpcomingWeek } from '../../api/types';
import { MOSTERT, playerDetail } from './fixtures';

/** A running back's game unless overridden. */
export function boxLine(overrides: Partial<ReplayBoxLine> = {}): ReplayBoxLine {
  return { pass_yds: 0, pass_td: 0, pass_int: 0, carries: 15, rush_yds: 72, rush_td: 1, targets: 4, rec: 3, rec_yds: 21, rec_td: 0, ...overrides };
}

export function defense(tier: ReplayDefenseRating['tier'], rank: number): ReplayDefenseRating {
  return { rank, of: 32, tier, factor: tier === 'easy' ? 1.15 : tier === 'tough' ? 0.85 : 1, allowed: 18.4 };
}

export function upcomingWeek(week: number, overrides: Partial<ReplayUpcomingWeek> = {}): ReplayUpcomingWeek {
  return { week, opponent: 'BUF', away: false, bye: false, estimate: 13.1, defense: null, out_expected: false, ...overrides };
}

/** playerDetail with box scores on played weeks, a defense rating on the current week and three weeks ahead. */
export function cardDetail(player: ReplayPlayerRef = MOSTERT): ReplayPlayerDetail {
  const detail = playerDetail(player);
  return {
    ...detail,
    weeks: detail.weeks.map((week) => ({
      ...week,
      box: week.played && !week.bye ? boxLine({ rush_yds: 50 + week.week }) : null,
      away: week.week === 2,
      defense: week.week === 5 ? defense('tough', 29) : null,
    })),
    upcoming: [
      upcomingWeek(6, { opponent: 'NYJ', away: true, defense: defense('easy', 3), estimate: 15.9 }),
      upcomingWeek(7, { opponent: null, bye: true, estimate: null }),
      upcomingWeek(8, { opponent: 'KC', estimate: null, out_expected: true }),
    ],
    defense_basis: { weeks: 4, prior: true },
  };
}
