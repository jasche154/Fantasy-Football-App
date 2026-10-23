import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import {
  applyThemePreference,
  loadThemePreference,
  resolveTheme,
  saveThemePreference,
  subscribeToSystemTheme,
  systemTheme,
  type ResolvedTheme,
  type ThemePreference,
} from './theme';

interface ThemeContextValue {
  preference: ThemePreference;
  resolved: ResolvedTheme;
  setPreference: (preference: ThemePreference) => void;
  /** Flips between light and dark (an explicit choice that overrides the system). */
  toggle: () => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreference] = useState<ThemePreference>(loadThemePreference);
  const [system, setSystem] = useState<ResolvedTheme>(systemTheme);

  useEffect(() => subscribeToSystemTheme(setSystem), []);

  useEffect(() => {
    applyThemePreference(preference);
    saveThemePreference(preference);
  }, [preference]);

  const resolved = resolveTheme(preference, system);

  const toggle = useCallback(() => {
    setPreference(resolved === 'dark' ? 'light' : 'dark');
  }, [resolved]);

  const value = useMemo(
    () => ({ preference, resolved, setPreference, toggle }),
    [preference, resolved, toggle],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (!context) throw new Error('useTheme must be used inside <ThemeProvider>.');
  return context;
}
