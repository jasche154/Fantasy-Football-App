import { buildQuery, type QueryParams } from '../lib/query';

export const DEFAULT_API_BASE = 'http://localhost:8000';

/** Normalises the configured base URL (falls back to the default, strips trailing slashes). */
export function resolveApiBase(raw: string | undefined): string {
  const value = raw?.trim();
  return (value ? value : DEFAULT_API_BASE).replace(/\/+$/, '');
}

export const API_BASE = resolveApiBase(import.meta.env.VITE_API_BASE);

const TOKEN_KEY = 'gridiron.apiToken';

/** The optional API token (backend API_TOKEN), kept in this browser only. */
export function getApiToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setApiToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // storage blocked (private mode): the token lasts for this page only
  }
}

/** Error raised for any failed API call. `status` is 0 when the server could not be reached. */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(message: string, status: number, detail: unknown = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }

  get isNetworkError(): boolean {
    return this.status === 0;
  }
}

const LOCATION_PREFIXES = new Set(['body', 'query', 'path']);

function formatValidationIssue(issue: unknown): string | null {
  if (typeof issue === 'string') return issue.trim() || null;
  if (!issue || typeof issue !== 'object') return null;
  const { loc, msg } = issue as { loc?: unknown; msg?: unknown };
  if (typeof msg !== 'string' || !msg.trim()) return null;
  const path = Array.isArray(loc)
    ? loc.filter((part) => !LOCATION_PREFIXES.has(String(part))).join('.')
    : '';
  return path ? `${path}: ${msg}` : msg;
}

/**
 * Turns a FastAPI `detail` value into readable text. `detail` is a string for most
 * errors and a list of `{loc, msg, type}` objects for 422 validation errors.
 */
export function detailToMessage(detail: unknown): string | null {
  if (Array.isArray(detail)) {
    const parts = detail
      .map(formatValidationIssue)
      .filter((part): part is string => part !== null);
    return parts.length > 0 ? parts.join('; ') : null;
  }
  return formatValidationIssue(detail);
}

export function isAbortError(error: unknown): boolean {
  return (
    typeof error === 'object' &&
    error !== null &&
    'name' in error &&
    (error as { name: unknown }).name === 'AbortError'
  );
}

/** User-facing message for anything thrown by an API call or a component. */
export function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return 'Something went wrong. Please try again.';
}

export interface RequestOptions {
  method?: 'GET' | 'POST';
  query?: QueryParams;
  body?: unknown;
  signal?: AbortSignal;
}

type ParsedBody = { ok: true; value: unknown } | { ok: false };

async function readBody(response: Response): Promise<ParsedBody> {
  let text: string;
  try {
    text = await response.text();
  } catch (error) {
    if (isAbortError(error)) throw error;
    return { ok: false };
  }
  if (!text) return { ok: true, value: null };
  try {
    return { ok: true, value: JSON.parse(text) as unknown };
  } catch {
    return { ok: false };
  }
}

function buildInit(options: RequestOptions): RequestInit {
  const hasBody = options.body !== undefined;
  const token = getApiToken();
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (hasBody) headers['Content-Type'] = 'application/json';
  if (token) headers.Authorization = `Bearer ${token}`;
  return {
    method: options.method ?? (hasBody ? 'POST' : 'GET'),
    signal: options.signal,
    headers,
    body: hasBody ? JSON.stringify(options.body) : undefined,
  };
}

async function send(url: string, options: RequestOptions): Promise<Response> {
  try {
    return await fetch(url, buildInit(options));
  } catch (error) {
    if (isAbortError(error)) throw error;
    throw new ApiError(
      `Could not reach the API at ${API_BASE}. Is the backend running?`,
      0,
    );
  }
}

function errorFromResponse(response: Response, body: ParsedBody): ApiError {
  const detail =
    body.ok && body.value && typeof body.value === 'object'
      ? (body.value as { detail?: unknown }).detail
      : undefined;
  const statusText = response.statusText ? ` ${response.statusText}` : '';
  const message =
    detailToMessage(detail) ?? `Request failed (HTTP ${response.status}${statusText}).`;
  return new ApiError(message, response.status, detail ?? null);
}

/** Performs a JSON request against the backend and returns the parsed body. */
export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const url = `${API_BASE}${path}${buildQuery(options.query ?? {})}`;
  const response = await send(url, options);
  const body = await readBody(response);
  if (!response.ok) throw errorFromResponse(response, body);
  if (!body.ok) {
    throw new ApiError('The server returned a response that was not valid JSON.', response.status);
  }
  return body.value as T;
}
