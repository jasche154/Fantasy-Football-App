import { describe, expect, it } from 'vitest';
import { buildHash, matchRoute, normalizePath, parseHash, parsePositiveInt } from './routes';

describe('parseHash', () => {
  it('maps the empty hash and "#/" to the dashboard', () => {
    expect(parseHash('').route).toEqual({ name: 'dashboard' });
    expect(parseHash('#/').route).toEqual({ name: 'dashboard' });
  });

  it('parses static routes, tolerating trailing slashes', () => {
    expect(parseHash('#/rankings').route).toEqual({ name: 'rankings' });
    expect(parseHash('#/stats/').route).toEqual({ name: 'stats' });
    expect(parseHash('#/market').route).toEqual({ name: 'market' });
    expect(parseHash('#/ask').route).toEqual({ name: 'ask' });
    expect(parseHash('#/settings').route).toEqual({ name: 'settings' });
  });

  it('parses the trade route with its shareable query', () => {
    const location = parseHash('#/trade?give=1,2&get=3');
    expect(location.route).toEqual({ name: 'trade' });
    expect(location.path).toBe('/trade');
    expect(location.query).toEqual({ give: '1,2', get: '3' });
  });

  it('parses player ids and rejects invalid ones', () => {
    expect(parseHash('#/player/42').route).toEqual({ name: 'player', id: 42 });
    expect(parseHash('#/player/42?season=2024').query).toEqual({ season: '2024' });
    expect(parseHash('#/player/abc').route).toEqual({ name: 'notFound', path: '/player/abc' });
    expect(parseHash('#/player/0').route.name).toBe('notFound');
    expect(parseHash('#/player').route.name).toBe('notFound');
    expect(parseHash('#/player/1/extra').route.name).toBe('notFound');
  });

  it('reports unknown paths as not found', () => {
    expect(parseHash('#/nope').route).toEqual({ name: 'notFound', path: '/nope' });
    expect(parseHash('#/rankings/extra').route.name).toBe('notFound');
  });

  it('accepts hashes without the leading #', () => {
    expect(parseHash('/rankings').route).toEqual({ name: 'rankings' });
  });
});

describe('helpers', () => {
  it('parsePositiveInt only accepts positive whole numbers', () => {
    expect(parsePositiveInt('7')).toBe(7);
    expect(parsePositiveInt('007')).toBe(7);
    expect(parsePositiveInt('1.5')).toBeNull();
    expect(parsePositiveInt('-3')).toBeNull();
    expect(parsePositiveInt('')).toBeNull();
    expect(parsePositiveInt(undefined)).toBeNull();
    expect(parsePositiveInt('99999999999999999999')).toBeNull();
  });

  it('normalizePath collapses slashes and decodes segments', () => {
    expect(normalizePath('//trade//')).toBe('/trade');
    expect(normalizePath('/a%20b')).toBe('/a b');
    expect(normalizePath('/%E0%A4%A')).toBe('/%E0%A4%A');
  });

  it('matchRoute handles the root path', () => {
    expect(matchRoute('/')).toEqual({ name: 'dashboard' });
  });

  it('buildHash keeps commas readable and drops empty params', () => {
    expect(buildHash('/trade', { give: '1,2', get: '3' })).toBe('#/trade?give=1,2&get=3');
    expect(buildHash('/trade', { give: undefined })).toBe('#/trade');
    expect(buildHash('player/5')).toBe('#/player/5');
  });

  it('round-trips through parseHash', () => {
    const hash = buildHash('/trade', { give: '10,11', get: '12' });
    expect(parseHash(hash).query).toEqual({ give: '10,11', get: '12' });
  });
});
