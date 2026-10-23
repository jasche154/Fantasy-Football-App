import { formatSignedFraction } from './format';

export type MatchupTone = 'easy' | 'neutral' | 'tough';

/** Factors within this distance of 1.0 read as neutral. */
const NEUTRAL_BAND = 0.03;
const EASY_RANK_MAX = 8;
const TOUGH_RANK_MIN = 25;

/** 1.14 -> "+14%": how much more (or less) this defense or schedule gives up than average. */
export function factorLabel(factor: number): string {
  return formatSignedFraction(factor - 1);
}

export function factorTone(factor: number): MatchupTone {
  if (factor >= 1 + NEUTRAL_BAND) return 'easy';
  if (factor <= 1 - NEUTRAL_BAND) return 'tough';
  return 'neutral';
}

/** Grade from the defense's rank at the position (1 = gives up the most). */
export function rankTone(rank: number | null): MatchupTone {
  if (rank === null) return 'neutral';
  if (rank <= EASY_RANK_MAX) return 'easy';
  if (rank >= TOUGH_RANK_MIN) return 'tough';
  return 'neutral';
}

export const TONE_LABELS: Readonly<Record<MatchupTone, string>> = {
  easy: 'Easy',
  neutral: 'Neutral',
  tough: 'Tough',
};
