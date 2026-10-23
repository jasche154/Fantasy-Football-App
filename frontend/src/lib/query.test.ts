import { describe, expect, it } from 'vitest';
import { DEFAULT_LEAGUE_SETTINGS } from '../state/leagueSettings';
import { buildQuery, settingsToParams } from './query';

describe('buildQuery', () => {
  it('serialises strings, numbers and booleans', () => {
    expect(buildQuery({ q: 'bijan', limit: 10, ascending: false })).toBe('?q=bijan&limit=10&ascending=false');
  });

  it('skips null, undefined, empty strings and non-finite numbers', () => {
    expect(buildQuery({ a: null, b: undefined, c: '', d: Number.NaN, e: 0 })).toBe('?e=0');
  });

  it('returns an empty string when nothing is left', () => {
    expect(buildQuery({})).toBe('');
    expect(buildQuery({ position: undefined })).toBe('');
  });

  it('encodes keys and values', () => {
    expect(buildQuery({ q: "Ja'Marr Chase & co" })).toBe("?q=Ja'Marr%20Chase%20%26%20co");
  });

  it('accepts a custom value encoder', () => {
    expect(buildQuery({ give: '1,2' }, (value) => value)).toBe('?give=1,2');
  });
});

describe('settingsToParams', () => {
  it('maps league settings to the shared backend params', () => {
    const params = settingsToParams({ scoring: 'half', teams: 10, superflex: true, te_premium: 0.5 });
    expect(params).toEqual({ scoring: 'half', teams: 10, superflex: true, te_premium: 0.5 });
    expect(buildQuery(params)).toBe('?scoring=half&teams=10&superflex=true&te_premium=0.5');
  });

  it('includes defaults explicitly', () => {
    expect(buildQuery(settingsToParams(DEFAULT_LEAGUE_SETTINGS))).toBe(
      '?scoring=ppr&teams=12&superflex=false&te_premium=0',
    );
  });
});
