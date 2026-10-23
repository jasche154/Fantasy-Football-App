import { useEffect, useRef, type ReactNode } from 'react';
import { BrandMark } from '../components/Icon';
import { APP_NAME } from '../hooks/useDocumentTitle';
import { buildHash, paths, type Route } from '../router/routes';
import { describeSettings } from '../state/leagueSettings';
import { useLeagueSettings } from '../state/SettingsContext';
import { MainNav } from './MainNav';
import { activeNavRoute } from './navItems';
import { ThemeToggle } from './ThemeToggle';

interface AppShellProps {
  route: Route;
  /** Changes when the page (not just its query) changes; used to move focus. */
  pageKey: string;
  children: ReactNode;
}

function useFocusOnPageChange(pageKey: string) {
  const mainRef = useRef<HTMLElement>(null);
  const previousKey = useRef(pageKey);
  useEffect(() => {
    if (previousKey.current === pageKey) return;
    previousKey.current = pageKey;
    const heading = mainRef.current?.querySelector<HTMLElement>('[data-page-heading]');
    // Focusing the page heading also scrolls it into view.
    (heading ?? mainRef.current)?.focus();
  }, [pageKey]);
  return mainRef;
}

export function AppShell({ route, pageKey, children }: AppShellProps) {
  const { settings } = useLeagueSettings();
  const mainRef = useFocusOnPageChange(pageKey);
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content" onClick={(event) => {
        event.preventDefault();
        mainRef.current?.focus();
      }}>
        Skip to content
      </a>
      <header className="topbar">
        <a className="brand" href={buildHash(paths.dashboard)}>
          <BrandMark />
          <span className="brand__name">{APP_NAME}</span>
        </a>
        <div className="topbar__actions">
          <a className="league-pill" href={buildHash(paths.settings)} title="League settings">
            <span className="sr-only">League settings: </span>
            {describeSettings(settings)}
          </a>
          <ThemeToggle />
        </div>
      </header>
      <MainNav active={activeNavRoute(route)} />
      <main id="main-content" className="main" ref={mainRef} tabIndex={-1}>
        {children}
      </main>
    </div>
  );
}
