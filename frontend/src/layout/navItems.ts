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
  /** Research pages sit together under a heading: useful, but not where weekly decisions are made. */
  group?: 'research';
}

export const NAV_ITEMS: readonly NavItem[] = [
  { route: 'dashboard', path: paths.dashboard, label: 'Dashboard', shortLabel: 'Home', icon: 'home', isPrimary: true },
  { route: 'team', path: paths.team, label: 'My Team', shortLabel: 'Team', icon: 'team', isPrimary: true },
  { route: 'matchup', path: paths.matchup, label: 'Matchup', shortLabel: 'Matchup', icon: 'trade', isPrimary: false },
  { route: 'weekly', path: paths.weekly, label: 'Week X Rankings', shortLabel: 'Week', icon: 'calendar', isPrimary: true },
  { route: 'waivers', path: paths.waivers, label: 'Waiver Wire', shortLabel: 'Waivers', icon: 'plus', isPrimary: false },
  { route: 'trade', path: paths.trade, label: 'Trade Calculator', shortLabel: 'Trade', icon: 'trade', isPrimary: true },
  { route: 'ask', path: paths.ask, label: 'Ask AI', shortLabel: 'Ask AI', icon: 'ask', isPrimary: false },
  { route: 'rankings', path: paths.rankings, label: 'Rankings', shortLabel: 'Ranks', icon: 'rankings', isPrimary: true, group: 'research' },
  { route: 'stats', path: paths.stats, label: 'Advanced Stats', shortLabel: 'Stats', icon: 'stats', isPrimary: false, group: 'research' },
  { route: 'charts', path: paths.charts, label: 'Chart Studio', shortLabel: 'Charts', icon: 'chart', isPrimary: false, group: 'research' },
  { route: 'compare', path: paths.compare, label: 'Compare Players', shortLabel: 'Compare', icon: 'swap', isPrimary: false, group: 'research' },
  { route: 'market', path: paths.market, label: 'Buy Low / Sell High', shortLabel: 'Market', icon: 'market', isPrimary: false, group: 'research' },
  { route: 'settings', path: paths.settings, label: 'Settings', shortLabel: 'Settings', icon: 'settings', isPrimary: false },
];

/** The nav label, with the real week number in place of the X once it's known. */
export function navLabel(item: NavItem, currentWeek: number | null | undefined): string {
  return item.route === 'weekly' && currentWeek ? `Week ${currentWeek} Rankings` : item.label;
}

/** Player pages highlight Rankings, since that's where players are browsed from. */
export function activeNavRoute(route: Route): Route['name'] {
  return route.name === 'player' ? 'rankings' : route.name;
}
