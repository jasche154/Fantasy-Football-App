const BARE_CODE_PATTERN = /^[A-Za-z0-9._~-]+$/;

function codeFromParams(query: string): string | null {
  const code = new URLSearchParams(query).get('code')?.trim();
  return code && BARE_CODE_PATTERN.test(code) ? code : null;
}

/**
 * Extracts the OAuth `code` from whatever the user pasted: the full redirected URL
 * (`https://localhost/callback?code=abc&state=xyz`), just its query string
 * (`?code=abc`), or the bare code itself. Returns null when no usable code is found.
 */
export function extractYahooCode(input: string): string | null {
  const value = input.trim();
  if (!value) return null;

  const queryStart = value.indexOf('?');
  if (queryStart >= 0) {
    return codeFromParams(value.slice(queryStart + 1).split('#')[0]);
  }
  if (value.includes('code=')) {
    return codeFromParams(value);
  }
  if (value.includes('://') || value.includes('/')) {
    return null;
  }
  return BARE_CODE_PATTERN.test(value) ? value : null;
}

/** Only http(s) URLs may be opened from API data. */
export function isSafeHttpUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' || url.protocol === 'http:';
  } catch {
    return false;
  }
}
