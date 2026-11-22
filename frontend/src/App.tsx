import type { ReactNode } from 'react';
import { AppShell } from './layout/AppShell';
import { GameShell } from './layout/GameShell';
import { AskPage } from './pages/ask/AskPage';
import { DashboardPage } from './pages/dashboard/DashboardPage';
import { MarketPage } from './pages/market/MarketPage';
import { OddsPage } from './pages/odds/OddsPage';
import { NotFoundPage } from './pages/NotFoundPage';
import { PlayerPage } from './pages/player/PlayerPage';
import { RankingsPage } from './pages/rankings/RankingsPage';
import { PlayPage, ReplayPage, toView } from './pages/replay/ReplayPage';
import { SettingsPage } from './pages/settings/SettingsPage';
import { StatsPage } from './pages/stats/StatsPage';
import { TradePage } from './pages/trade/TradePage';
import { WeeklyPage } from './pages/weekly/WeeklyPage';
import { MyTeamPage } from './pages/team/MyTeamPage';
import { MatchupPage } from './pages/matchup/MatchupPage';
import { ChartsPage } from './pages/charts/ChartsPage';
import { ComparePage } from './pages/compare/ComparePage';
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
    case 'team':
      return <MyTeamPage />;
    case 'matchup':
      return <MatchupPage />;
    case 'rankings':
      return <RankingsPage />;
    case 'weekly':
      return <WeeklyPage />;
    case 'charts':
      return <ChartsPage />;
    case 'compare':
      return <ComparePage query={query} />;
    case 'waivers':
      return <WaiversPage />;
    case 'stats':
      return <StatsPage />;
    case 'trade':
      return <TradePage query={query} />;
    case 'market':
      return <MarketPage />;
    case 'odds':
      return <OddsPage query={query} />;
    case 'player':
      return <PlayerPage key={route.id} playerId={route.id} query={query} />;
    case 'ask':
      return <AskPage />;
    case 'replay':
      return <ReplayPage query={query} />;
    case 'play':
      return <PlayPage query={query} />;
    case 'settings':
      return <SettingsPage />;
    case 'notFound':
      return <NotFoundPage path={route.path} />;
  }
}

function Routes() {
  const location = useHashLocation();
  if (location.route.name === 'play') {
    // The game is full screen: its own shell, with Exit back to the dashboard's #/replay. An unknown view is the team view, as in PlayPage.
    return (
      <GameShell gameKey={location.query.game ?? ''} viewKey={toView(location.query.view)}>
        {renderRoute(location)}
      </GameShell>
    );
  }
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
