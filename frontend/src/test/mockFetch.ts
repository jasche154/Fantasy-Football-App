import { vi } from 'vitest';

export interface MockRequest {
  url: URL;
  method: string;
  body: unknown;
}

export interface MockReply {
  status?: number;
  body?: unknown;
  /** Raw response text (e.g. invalid JSON); overrides `body`. */
  raw?: string;
  statusText?: string;
}

type Handler = (request: MockRequest) => MockReply | Promise<MockReply>;

export interface MockRoute {
  method?: 'GET' | 'POST';
  path: string | RegExp;
  reply: MockReply | Handler;
}

export function json(body: unknown, status = 200): MockReply {
  return { body, status };
}

function toUrl(input: RequestInfo | URL): URL {
  if (typeof input === 'string') return new URL(input);
  if (input instanceof URL) return input;
  return new URL(input.url);
}

/** Rejects like real fetch when the request's signal aborts before the reply is ready. */
function withAbort<T>(promise: Promise<T>, signal: AbortSignal | undefined): Promise<T> {
  if (!signal) return promise;
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(new DOMException('The operation was aborted.', 'AbortError'));
    signal.addEventListener('abort', onAbort, { once: true });
    promise.then(resolve, reject).finally(() => signal.removeEventListener('abort', onAbort));
  });
}

function matches(route: MockRoute, method: string, pathname: string): boolean {
  if ((route.method ?? 'GET') !== method) return false;
  return typeof route.path === 'string' ? route.path === pathname : route.path.test(pathname);
}

/**
 * Replaces global fetch with a router over `routes`. Unmatched requests get a 500 whose
 * detail names the request, so a missing mock shows up clearly in assertions.
 */
export function mockFetch(routes: readonly MockRoute[]) {
  const calls: MockRequest[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.signal?.aborted) throw new DOMException('The operation was aborted.', 'AbortError');
    const url = toUrl(input);
    const method = init?.method ?? 'GET';
    const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined;
    const request: MockRequest = { url, method, body };
    calls.push(request);
    const route = routes.find((candidate) => matches(candidate, method, url.pathname));
    if (!route) {
      return new Response(JSON.stringify({ detail: `Unmocked ${method} ${url.pathname}` }), { status: 500 });
    }
    const reply = await withAbort(
      Promise.resolve(typeof route.reply === 'function' ? route.reply(request) : route.reply),
      init?.signal ?? undefined,
    );
    const text = reply.raw ?? JSON.stringify(reply.body ?? null);
    return new Response(text, {
      status: reply.status ?? 200,
      statusText: reply.statusText,
      headers: { 'Content-Type': 'application/json' },
    });
  });
  vi.stubGlobal('fetch', fn);
  return {
    fn,
    calls,
    callsTo: (pathname: string, method = 'GET') =>
      calls.filter((call) => call.url.pathname === pathname && call.method === method),
  };
}
