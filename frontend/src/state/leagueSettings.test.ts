import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  DEFAULT_LEAGUE_SETTINGS,
  describeSettings,
  loadLeagueSettings,
  sanitizeLeagueSettings,
  saveLeagueSettings,
  scoringLabel,
  SETTINGS_STORAGE_KEY,
} from './leagueSettings';

afterEach(() => {
  vi.restoreAllMocks();
});

describe('sanitizeLeagueSettings', () => {
  it('keeps valid fields', () => {
    const settings = { scoring: 'half', teams: 10, superflex: true, te_premium: 0.5 };
    expect(sanitizeLeagueSettings(settings)).toEqual(settings);
  });

  it('replaces each invalid field with its default', () => {
    expect(
      sanitizeLeagueSettings({ scoring: 'full', teams: 3, superflex: 'yes', te_premium: 2 }),
    ).toEqual(DEFAULT_LEAGUE_SETTINGS);
    expect(sanitizeLeagueSettings({ teams: 12.5 }).teams).toBe(12);
    expect(sanitizeLeagueSettings({ teams: 21 }).teams).toBe(12);
    expect(sanitizeLeagueSettings({ te_premium: -0.1 }).te_premium).toBe(0);
  });

  it('falls back to defaults for non-objects', () => {
    expect(sanitizeLeagueSettings(null)).toEqual(DEFAULT_LEAGUE_SETTINGS);
    expect(sanitizeLeagueSettings('ppr')).toEqual(DEFAULT_LEAGUE_SETTINGS);
  });
});

describe('persistence', () => {
  it('round-trips through localStorage', () => {
    const settings = { scoring: 'std' as const, teams: 14, superflex: false, te_premium: 0.25 };
    expect(saveLeagueSettings(settings)).toBe(true);
    expect(loadLeagueSettings()).toEqual(settings);
  });

  it('ignores corrupted stored JSON', () => {
    window.localStorage.setItem(SETTINGS_STORAGE_KEY, '{not json');
    expect(loadLeagueSettings()).toEqual(DEFAULT_LEAGUE_SETTINGS);
  });

  it('survives storage that throws', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError');
    });
    expect(loadLeagueSettings()).toEqual(DEFAULT_LEAGUE_SETTINGS);
    expect(saveLeagueSettings(DEFAULT_LEAGUE_SETTINGS)).toBe(false);
  });
});

describe('labels', () => {
  it('describes settings in one line', () => {
    expect(describeSettings(DEFAULT_LEAGUE_SETTINGS)).toBe('12-team · PPR');
    expect(describeSettings({ scoring: 'half', teams: 10, superflex: true, te_premium: 0.5 })).toBe(
      '10-team · Half PPR · Superflex · TE +0.5',
    );
  });

  it('labels scoring presets', () => {
    expect(scoringLabel('std')).toBe('Standard');
  });
});
