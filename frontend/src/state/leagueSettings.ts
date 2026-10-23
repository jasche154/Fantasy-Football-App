import type { LeagueSettings, Scoring } from '../api/types';
import { readJsonStorage, writeJsonStorage } from '../lib/storage';

export const SETTINGS_STORAGE_KEY = 'gridiron-edge.league-settings.v1';

export const TEAMS_MIN = 4;
export const TEAMS_MAX = 20;
export const TE_PREMIUM_MIN = 0;
export const TE_PREMIUM_MAX = 1;

export const SCORING_OPTIONS: ReadonlyArray<{ value: Scoring; label: string; short: string }> = [
  { value: 'ppr', label: 'Full PPR (1 pt / reception)', short: 'PPR' },
  { value: 'half', label: 'Half PPR (0.5 pt / reception)', short: 'Half PPR' },
  { value: 'std', label: 'Standard (no reception points)', short: 'Standard' },
];

export const DEFAULT_LEAGUE_SETTINGS: Readonly<LeagueSettings> = Object.freeze({
  scoring: 'ppr',
  teams: 12,
  superflex: false,
  te_premium: 0,
});

export function isScoring(value: unknown): value is Scoring {
  return value === 'ppr' || value === 'half' || value === 'std';
}

export function isValidTeams(value: unknown): value is number {
  return (
    typeof value === 'number' && Number.isInteger(value) && value >= TEAMS_MIN && value <= TEAMS_MAX
  );
}

export function isValidTePremium(value: unknown): value is number {
  return (
    typeof value === 'number' &&
    Number.isFinite(value) &&
    value >= TE_PREMIUM_MIN &&
    value <= TE_PREMIUM_MAX
  );
}

/** Accepts anything (e.g. parsed localStorage) and returns valid settings, field by field. */
export function sanitizeLeagueSettings(raw: unknown): LeagueSettings {
  const source = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {};
  return {
    scoring: isScoring(source.scoring) ? source.scoring : DEFAULT_LEAGUE_SETTINGS.scoring,
    teams: isValidTeams(source.teams) ? source.teams : DEFAULT_LEAGUE_SETTINGS.teams,
    superflex:
      typeof source.superflex === 'boolean' ? source.superflex : DEFAULT_LEAGUE_SETTINGS.superflex,
    te_premium: isValidTePremium(source.te_premium)
      ? source.te_premium
      : DEFAULT_LEAGUE_SETTINGS.te_premium,
  };
}

export function loadLeagueSettings(): LeagueSettings {
  return sanitizeLeagueSettings(readJsonStorage(SETTINGS_STORAGE_KEY));
}

export function saveLeagueSettings(settings: LeagueSettings): boolean {
  return writeJsonStorage(SETTINGS_STORAGE_KEY, settings);
}

export function scoringLabel(scoring: Scoring): string {
  return SCORING_OPTIONS.find((option) => option.value === scoring)?.short ?? scoring;
}

/** One-line description, e.g. "12-team · Half PPR · Superflex · TE +0.5". */
export function describeSettings(settings: LeagueSettings): string {
  const parts = [`${settings.teams}-team`, scoringLabel(settings.scoring)];
  if (settings.superflex) parts.push('Superflex');
  if (settings.te_premium > 0) parts.push(`TE +${settings.te_premium}`);
  return parts.join(' · ');
}
