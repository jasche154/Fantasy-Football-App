import type { Meta } from '../../api/types';
import { PageHeader } from '../../components/Card';
import { Icon, type IconName } from '../../components/Icon';
import { useDocumentTitle } from '../../hooks/useDocumentTitle';
import { buildHash, paths } from '../../router/routes';
import { describeSettings } from '../../state/leagueSettings';
import { useMeta } from '../../state/MetaContext';
import { useLeagueSettings } from '../../state/SettingsContext';
import { InsightCard } from './InsightCard';
import { TrendingCard } from './TrendingCard';

export function freshnessLabel(meta: Meta): string {
  return meta.latest_week === null
    ? `${meta.current_season} season · no games played yet`
    : `${meta.current_season} season · stats through week ${meta.latest_week}`;
}

function Freshness() {
  const meta = useMeta();
  if (meta.status === 'error') {
    return (
      <span className="freshness freshness--error" role="status">
        Data freshness unavailable: {meta.error.message}
      </span>
    );
  }
  if (!meta.data) {
    return <span className="freshness freshness--loading" role="status">Checking data freshness…</span>;
  }
  return (
    <span className="freshness" role="status">
      <span className="freshness__dot" aria-hidden="true" />
      {freshnessLabel(meta.data)}
    </span>
  );
}

const QUICK_LINKS: ReadonlyArray<{ path: string; label: string; text: string; icon: IconName }> = [
  { path: paths.trade, label: 'Trade calculator', text: 'Value any deal with package discounts.', icon: 'trade' },
  { path: paths.rankings, label: 'Rankings', text: 'Actual vs expected vs model projections.', icon: 'rankings' },
  { path: paths.stats, label: 'Advanced stats', text: 'Volume-weighted NGS & efficiency leaderboards.', icon: 'stats' },
  { path: paths.ask, label: 'Ask AI', text: 'Questions answered from real tool calls.', icon: 'ask' },
];

function QuickLinks() {
  return (
    <nav className="quick-links" aria-label="Quick links">
      {QUICK_LINKS.map((link) => (
        <a key={link.path} className="quick-link" href={buildHash(link.path)}>
          <span className="quick-link__icon"><Icon name={link.icon} /></span>
          <span className="quick-link__label">{link.label}</span>
          <span className="quick-link__text">{link.text}</span>
        </a>
      ))}
    </nav>
  );
}

export function DashboardPage() {
  useDocumentTitle('Dashboard');
  const { settings } = useLeagueSettings();
  return (
    <>
      <PageHeader
        title="Dashboard"
        subtitle={
          <>
            <Freshness />
            <span className="page-header__sep" aria-hidden="true">·</span>
            <span>{describeSettings(settings)}</span>
          </>
        }
      />
      <QuickLinks />
      <div className="dashboard-grid">
        <InsightCard kind="buy-low" />
        <InsightCard kind="sell-high" />
        <TrendingCard />
      </div>
    </>
  );
}
