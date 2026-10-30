import { describe, expect, it, vi } from 'vitest';
import { json, mockFetch } from '../test/mockFetch';
import {
  API_BASE,
  ApiError,
  apiRequest,
  detailToMessage,
  errorMessage,
  getApiToken,
  isAbortError,
  resolveApiBase,
  setApiToken,
} from './client';

describe('resolveApiBase', () => {
  it('defaults to localhost:8000 and strips trailing slashes', () => {
    expect(resolveApiBase(undefined)).toBe('http://localhost:8000');
    expect(resolveApiBase('  ')).toBe('http://localhost:8000');
    expect(resolveApiBase('https://api.example.com///')).toBe('https://api.example.com');
    expect(API_BASE).toBe('http://localhost:8000');
  });
});

describe('detailToMessage', () => {
  it('passes string details through', () => {
    expect(detailToMessage('Player not found')).toBe('Player not found');
    expect(detailToMessage('   ')).toBeNull();
  });

  it('joins FastAPI validation errors with their field path', () => {
    const detail = [
      { loc: ['body', 'give'], msg: 'List should have at least 1 item', type: 'too_short' },
      { loc: ['query', 'teams'], msg: 'Input should be less than or equal to 20', type: 'less_than_equal' },
    ];
    expect(detailToMessage(detail)).toBe(
      'give: List should have at least 1 item; teams: Input should be less than or equal to 20',
    );
  });

  it('handles odd detail shapes', () => {
    expect(detailToMessage([{ msg: 'Bad input' }, 42, null])).toBe('Bad input');
    expect(detailToMessage(['plain string'])).toBe('plain string');
    expect(detailToMessage([])).toBeNull();
    expect(detailToMessage({ msg: 'object detail' })).toBe('object detail');
    expect(detailToMessage({ unexpected: true })).toBeNull();
    expect(detailToMessage(undefined)).toBeNull();
  });
});

describe('apiRequest', () => {
  it('returns parsed JSON and sends query params', async () => {
    const api = mockFetch([{ path: '/api/rankings', reply: json({ results: [] }) }]);
    await expect(apiRequest('/api/rankings', { query: { position: 'RB', limit: 5, skip: undefined } })).resolves.toEqual({
      results: [],
    });
    const [call] = api.calls;
    expect(call.url.href).toBe('http://localhost:8000/api/rankings?position=RB&limit=5');
    expect(call.method).toBe('GET');
  });

  it('POSTs JSON bodies with a content type', async () => {
    const api = mockFetch([{ method: 'POST', path: '/api/trade/evaluate', reply: json({ ok: true }) }]);
    await apiRequest('/api/trade/evaluate', { body: { give: [1], get: [2] } });
    const init = api.fn.mock.calls[0][1];
    expect(init?.method).toBe('POST');
    expect(init?.headers).toMatchObject({ 'Content-Type': 'application/json' });
    expect(api.calls[0].body).toEqual({ give: [1], get: [2] });
  });

  it('raises ApiError with the FastAPI string detail', async () => {
    mockFetch([{ path: '/api/players/9', reply: json({ detail: 'Player 9 not found' }, 404) }]);
    const error = await apiRequest('/api/players/9').catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 404, message: 'Player 9 not found', detail: 'Player 9 not found' });
    expect((error as ApiError).isNetworkError).toBe(false);
  });

  it('raises ApiError with joined validation messages on 422', async () => {
    mockFetch([
      {
        method: 'POST',
        path: '/api/trade/evaluate',
        reply: json({ detail: [{ loc: ['body', 'get'], msg: 'Unknown player id 999' }] }, 422),
      },
    ]);
    await expect(apiRequest('/api/trade/evaluate', { method: 'POST', body: {} })).rejects.toMatchObject({
      status: 422,
      message: 'get: Unknown player id 999',
    });
  });

  it('falls back to the HTTP status when the error body has no detail', async () => {
    mockFetch([{ path: '/api/meta', reply: { status: 500, raw: '<html>oops</html>', statusText: 'Internal Server Error' } }]);
    await expect(apiRequest('/api/meta')).rejects.toMatchObject({
      status: 500,
      message: 'Request failed (HTTP 500 Internal Server Error).',
    });
  });

  it('rejects successful responses that are not JSON', async () => {
    mockFetch([{ path: '/api/meta', reply: { status: 200, raw: 'not json' } }]);
    await expect(apiRequest('/api/meta')).rejects.toMatchObject({
      status: 200,
      message: 'The server returned a response that was not valid JSON.',
    });
  });

  it('treats an empty body as null', async () => {
    mockFetch([{ path: '/api/health', reply: { status: 200, raw: '' } }]);
    await expect(apiRequest('/api/health')).resolves.toBeNull();
  });

  it('maps network failures to a status-0 ApiError', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    const error = await apiRequest('/api/meta').catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).isNetworkError).toBe(true);
    expect((error as ApiError).message).toContain('Could not reach the API at http://localhost:8000');
  });

  it('lets abort errors through untouched', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new DOMException('aborted', 'AbortError')));
    const error = await apiRequest('/api/meta').catch((caught: unknown) => caught);
    expect(isAbortError(error)).toBe(true);
    expect(error).not.toBeInstanceOf(ApiError);
  });
});

describe('errorMessage', () => {
  it('returns error messages or a generic fallback', () => {
    expect(errorMessage(new Error('Boom'))).toBe('Boom');
    expect(errorMessage('weird')).toBe('Something went wrong. Please try again.');
    expect(errorMessage(new Error(''))).toBe('Something went wrong. Please try again.');
  });

  it('only recognises AbortError-named errors as aborts', () => {
    expect(isAbortError(new Error('x'))).toBe(false);
    expect(isAbortError(null)).toBe(false);
  });
});

describe('API token', () => {
  it('sends the saved token as a Bearer header and stops after it is forgotten', async () => {
    const api = mockFetch([{ path: '/api/meta', reply: json({}) }]);
    setApiToken('abc123');
    await apiRequest('/api/meta');
    const headers = api.fn.mock.calls[0][1]?.headers as Record<string, string>;
    expect(headers.Authorization).toBe('Bearer abc123');
    setApiToken(null);
    expect(getApiToken()).toBeNull();
    await apiRequest('/api/meta');
    expect((api.fn.mock.calls[1][1]?.headers as Record<string, string>).Authorization).toBeUndefined();
  });
});
