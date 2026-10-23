import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ThemeToggle } from '../layout/ThemeToggle';
import { ThemeProvider } from './ThemeContext';
import {
  applyThemePreference,
  loadThemePreference,
  resolveTheme,
  saveThemePreference,
  subscribeToSystemTheme,
  systemTheme,
  THEME_STORAGE_KEY,
} from './theme';

function stubMatchMedia(prefersLight: boolean) {
  const listeners: Array<(event: MediaQueryListEvent) => void> = [];
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({
      matches: prefersLight,
      addEventListener: (_: string, listener: (event: MediaQueryListEvent) => void) => listeners.push(listener),
      removeEventListener: vi.fn(),
    })),
  );
  return listeners;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('theme helpers', () => {
  it('defaults to dark when matchMedia is unavailable (dark-first)', () => {
    expect(systemTheme()).toBe('dark');
    expect(subscribeToSystemTheme(() => undefined)).toBeTypeOf('function');
  });

  it('follows prefers-color-scheme and notifies on change', () => {
    const listeners = stubMatchMedia(true);
    expect(systemTheme()).toBe('light');
    const onChange = vi.fn();
    subscribeToSystemTheme(onChange);
    listeners[0]({ matches: false } as MediaQueryListEvent);
    expect(onChange).toHaveBeenCalledWith('dark');
  });

  it('resolves explicit preferences over the system theme', () => {
    expect(resolveTheme('system', 'light')).toBe('light');
    expect(resolveTheme('dark', 'light')).toBe('dark');
  });

  it('persists explicit choices and clears "system"', () => {
    saveThemePreference('light');
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('light');
    expect(loadThemePreference()).toBe('light');
    saveThemePreference('system');
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
    window.localStorage.setItem(THEME_STORAGE_KEY, 'purple');
    expect(loadThemePreference()).toBe('system');
  });

  it('applies data-theme only for explicit choices', () => {
    applyThemePreference('light');
    expect(document.documentElement.dataset.theme).toBe('light');
    applyThemePreference('system');
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
  });
});

describe('ThemeToggle', () => {
  it('flips the theme and remembers the choice', async () => {
    const user = userEvent.setup();
    render(
      <ThemeProvider>
        <ThemeToggle />
      </ThemeProvider>,
    );
    await user.click(screen.getByRole('button', { name: 'Switch to light theme' }));
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('light');
    await user.click(screen.getByRole('button', { name: 'Switch to dark theme' }));
    expect(document.documentElement.dataset.theme).toBe('dark');
  });
});
