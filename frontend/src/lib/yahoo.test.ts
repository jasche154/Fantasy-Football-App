import { describe, expect, it } from 'vitest';
import { extractYahooCode, isSafeHttpUrl } from './yahoo';

describe('extractYahooCode', () => {
  it('pulls the code out of the full redirected URL', () => {
    expect(extractYahooCode('https://localhost/callback?code=abc123&state=xyz')).toBe('abc123');
  });

  it('ignores surrounding whitespace and URL fragments', () => {
    expect(extractYahooCode('  https://localhost/callback?code=k9x2#done  ')).toBe('k9x2');
  });

  it('accepts a bare query string or code= fragment', () => {
    expect(extractYahooCode('?code=q1w2e3')).toBe('q1w2e3');
    expect(extractYahooCode('code=q1w2e3&state=1')).toBe('q1w2e3');
  });

  it('accepts the bare code itself', () => {
    expect(extractYahooCode('hf8sd7f8s')).toBe('hf8sd7f8s');
  });

  it('returns null when there is no usable code', () => {
    expect(extractYahooCode('')).toBeNull();
    expect(extractYahooCode('   ')).toBeNull();
    expect(extractYahooCode('https://localhost/callback?error=access_denied')).toBeNull();
    expect(extractYahooCode('https://localhost/callback')).toBeNull();
    expect(extractYahooCode('two words')).toBeNull();
    expect(extractYahooCode('https://localhost/callback?code=<script>')).toBeNull();
  });
});

describe('isSafeHttpUrl', () => {
  it('allows http and https only', () => {
    expect(isSafeHttpUrl('https://api.login.yahoo.com/oauth2/request_auth?x=1')).toBe(true);
    expect(isSafeHttpUrl('http://localhost:8000')).toBe(true);
    expect(isSafeHttpUrl('javascript:alert(1)')).toBe(false);
    expect(isSafeHttpUrl('not a url')).toBe(false);
  });
});
