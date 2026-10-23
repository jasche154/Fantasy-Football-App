import { readStorage, writeStorage } from '../lib/storage';

export type ThemePreference = 'system' | 'light' | 'dark';
export type ResolvedTheme = 'light' | 'dark';

export const THEME_STORAGE_KEY = 'gridiron-edge.theme.v1';

export function isThemePreference(value: unknown): value is ThemePreference {
  return value === 'system' || value === 'light' || value === 'dark';
}

export function loadThemePreference(): ThemePreference {
  const stored = readStorage(THEME_STORAGE_KEY);
  return isThemePreference(stored) ? stored : 'system';
}

export function saveThemePreference(preference: ThemePreference): boolean {
  return writeStorage(THEME_STORAGE_KEY, preference === 'system' ? null : preference);
}

const LIGHT_QUERY = '(prefers-color-scheme: light)';

/** The app is dark-first: without a light-scheme signal we resolve to dark. */
export function systemTheme(): ResolvedTheme {
  if (typeof window.matchMedia !== 'function') return 'dark';
  return window.matchMedia(LIGHT_QUERY).matches ? 'light' : 'dark';
}

export function resolveTheme(preference: ThemePreference, system: ResolvedTheme): ResolvedTheme {
  return preference === 'system' ? system : preference;
}

export function subscribeToSystemTheme(onChange: (theme: ResolvedTheme) => void): () => void {
  if (typeof window.matchMedia !== 'function') return () => undefined;
  const query = window.matchMedia(LIGHT_QUERY);
  const listener = (event: MediaQueryListEvent) => onChange(event.matches ? 'light' : 'dark');
  query.addEventListener('change', listener);
  return () => query.removeEventListener('change', listener);
}

/** Reflects an explicit choice on <html data-theme>; "system" leaves it to the media query. */
export function applyThemePreference(preference: ThemePreference): void {
  const root = document.documentElement;
  if (preference === 'system') {
    root.removeAttribute('data-theme');
  } else {
    root.setAttribute('data-theme', preference);
  }
}
