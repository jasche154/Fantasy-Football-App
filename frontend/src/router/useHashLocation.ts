import { useMemo, useSyncExternalStore } from 'react';
import type { QueryParams } from '../lib/query';
import { buildHash, parseHash, type HashLocation } from './routes';

function subscribe(onChange: () => void): () => void {
  window.addEventListener('hashchange', onChange);
  return () => window.removeEventListener('hashchange', onChange);
}

function getSnapshot(): string {
  return window.location.hash;
}

function getServerSnapshot(): string {
  return '';
}

/** Current hash route, re-rendering on every `hashchange`. */
export function useHashLocation(): HashLocation {
  const hash = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  return useMemo(() => parseHash(hash), [hash]);
}

export interface NavigateOptions {
  /** Replace the current history entry instead of pushing a new one. */
  replace?: boolean;
}

/** Navigates to a hash route. `replace` keeps edits (e.g. trade changes) out of history. */
export function navigate(path: string, query: QueryParams = {}, options: NavigateOptions = {}): void {
  const hash = buildHash(path, query);
  if (hash === window.location.hash) return;
  if (options.replace) {
    const url = `${window.location.pathname}${window.location.search}${hash}`;
    window.history.replaceState(window.history.state, '', url);
    window.dispatchEvent(new Event('hashchange'));
    return;
  }
  window.location.hash = hash;
}
