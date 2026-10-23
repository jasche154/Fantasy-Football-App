import type { ReactNode } from 'react';
import { AppShell } from './layout/AppShell';
import { AskPage } from './pages/ask/AskPage';
import { DashboardPage } from './pages/dashboard/DashboardPage';
import { MarketPage } from './pages/market/MarketPage';
import { NotFoundPage } from './pages/NotFoundPage';
import { PlayerPage } from './pages/player/PlayerPage';
import { RankingsPage } from './pages/rankings/RankingsPage';
import { SettingsPage } from './pages/settings/SettingsPage';
import { StatsPage } from './pages/stats/StatsPage';
import { TradePage } from './pages/trade/TradePage';
import { WeeklyPage } from './pages/weekly/WeeklyPage';
import { ChartsPage } from './pages/charts/ChartsPage';
import { WaiversPage } from './pages/waivers/WaiversPage';
import type { HashLocation } from './router/routes';
import { useHashLocation } from './router/useHashLocation';
import { MetaProvider } from './state/MetaContext';
import { SettingsProvider } from './state/SettingsContext';
import { ThemeProvider } from './state/ThemeContext';

function renderRoute(location: HashLocation): ReactNode {
  const { route, query } = location;
  switch (route.name) {
    case 'dashboard':
      return <DashboardPage />;
    case 'rankings':
      return <RankingsPage />;
    case 'weekly':
      return <WeeklyPage />;
    case 'charts':
      return <ChartsPage />;
    case 'waivers':
      return <WaiversPage />;
    case 'stats':
      return <StatsPage />;
    case 'trade':
      return <TradePage query={query} />;
    case 'market':
      return <MarketPage />;
    case 'player':
      return <PlayerPage key={route.id} playerId={route.id} query={query} />;
    case 'ask':
      return <AskPage />;
    case 'settings':
      return <SettingsPage />;
    case 'notFound':
      return <NotFoundPage path={route.path} />;
  }
}

function Routes() {
  const location = useHashLocation();
  return (
    <AppShell route={location.route} pageKey={location.path}>
      {renderRoute(location)}
    </AppShell>
  );
}

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider>
      <SettingsProvider>
        <MetaProvider>{children}</MetaProvider>
      </SettingsProvider>
    </ThemeProvider>
  );
}

export function App() {
  return (
    <AppProviders>
      <Routes />
    </AppProviders>
  );
}
