/** Fixtures for the My Team and Matchup tests (on top of fixtures.ts). */
import type { ReplayDefenseRating, ReplayTeam } from '../../api/types';
import { COOK, KELCE, MAHOMES, OLAVE, rosterRow, team } from './fixtures';

export function defenseRating(overrides: Partial<ReplayDefenseRating> = {}): ReplayDefenseRating {
  return { rank: 3, of: 32, tier: 'easy', factor: 1.14, allowed: 21.6, ...overrides };
}

/** The user's week 3 as played: two starters with points and a recorded bench (Cook 14.2 and Olave 4.8 left there). */
export function playedTeam(overrides: Partial<ReplayTeam> = {}): ReplayTeam {
  return team({
    week: 3, played: true, actual_total: 39.3, bench_total: 19, bench_recorded: true,
    results: [
      rosterRow(MAHOMES, 'QB', { points: 24.1 }), rosterRow(KELCE, 'TE', { points: 15.2 }),
      rosterRow(COOK, 'BN', { points: 14.2 }), rosterRow(OLAVE, 'BN', { points: 4.8 }),
    ],
    ...overrides,
  });
}
