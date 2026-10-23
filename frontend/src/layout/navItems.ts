import type { IconName } from '../components/Icon';
import type { Route } from '../router/routes';
import { paths } from '../router/routes';

export interface NavItem {
  route: Route['name'];
  path: string;
  label: string;
  shortLabel: string;
  icon: IconName;
  /** Primary items appear in the phone tab bar; the rest live under "More". */
  isPrimary: boolean;
}

export const NAV_ITEMS: readonly NavItem[] = [
  { route: 'dashboard', path: paths.dashboard, label: 'Dashboard', shortLabel: 'Home', icon: 'home', isPrimary: true },
  { route: 'rankings', path: paths.rankings, label: 'Rankings', shortLabel: 'Ranks', icon: 'rankings', isPrimary: true },
  { route: 'weekly', path: paths.weekly, label: 'Week X Rankings', shortLabel: 'Week', icon: 'calendar', isPrimary: true },
  { route: 'stats', path: paths.stats, label: 'Advanced Stats', shortLabel: 'Stats', icon: 'stats', isPrimary: false },
  { route: 'waivers', path: paths.waivers, label: 'Waiver Wire', shortLabel: 'Waivers', icon: 'plus', isPrimary: false },
  { route: 'charts', path: paths.charts, label: 'Chart Studio', shortLabel: 'Charts', icon: 'chart', isPrimary: false },
  { route: 'trade', path: paths.trade, label: 'Trade Calculator', shortLabel: 'Trade', icon: 'trade', isPrimary: true },
  { route: 'market', path: paths.market, label: 'Buy Low / Sell High', shortLabel: 'Market', icon: 'market', isPrimary: false },
  { route: 'ask', path: paths.ask, label: 'Ask AI', shortLabel: 'Ask AI', icon: 'ask', isPrimary: false },
  { route: 'settings', path: paths.settings, label: 'Settings', shortLabel: 'Settings', icon: 'settings', isPrimary: false },
];

/** Player pages highlight Rankings, since that's where players are browsed from. */
export function activeNavRoute(route: Route): Route['name'] {
  return route.name === 'player' ? 'rankings' : route.name;
}
