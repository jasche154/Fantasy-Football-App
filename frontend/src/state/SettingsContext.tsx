import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import type { LeagueSettings } from '../api/types';
import {
  DEFAULT_LEAGUE_SETTINGS,
  loadLeagueSettings,
  sanitizeLeagueSettings,
  saveLeagueSettings,
} from './leagueSettings';

interface SettingsContextValue {
  settings: LeagueSettings;
  /** Merges a partial update; invalid fields fall back to their current/default values. */
  updateSettings: (patch: Partial<LeagueSettings>) => void;
  resetSettings: () => void;
  /** False when the browser refused to persist the last change. */
  persisted: boolean;
}

const SettingsContext = createContext<SettingsContextValue | null>(null);

interface SettingsProviderProps {
  children: ReactNode;
  initialSettings?: LeagueSettings;
}

export function SettingsProvider({ children, initialSettings }: SettingsProviderProps) {
  const [settings, setSettings] = useState<LeagueSettings>(
    () => initialSettings ?? loadLeagueSettings(),
  );
  const [persisted, setPersisted] = useState(true);

  useEffect(() => {
    setPersisted(saveLeagueSettings(settings));
  }, [settings]);

  const updateSettings = useCallback((patch: Partial<LeagueSettings>) => {
    setSettings((previous) => sanitizeLeagueSettings({ ...previous, ...patch }));
  }, []);

  const resetSettings = useCallback(() => {
    setSettings({ ...DEFAULT_LEAGUE_SETTINGS });
  }, []);

  const value = useMemo(
    () => ({ settings, updateSettings, resetSettings, persisted }),
    [settings, updateSettings, resetSettings, persisted],
  );

  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>;
}

export function useLeagueSettings(): SettingsContextValue {
  const context = useContext(SettingsContext);
  if (!context) throw new Error('useLeagueSettings must be used inside <SettingsProvider>.');
  return context;
}
