import { getLeagueWaivers, getMyTeam } from '../../api/data';
import type { MyLeague } from '../../api/types';
import { Card } from '../../components/Card';
import { Skeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { formatNumber } from '../../lib/format';
import { buildHash, paths } from '../../router/routes';
import { useMyLeague } from '../../state/useMyLeague';

function nextLock(kickoffs: (string | null | undefined)[]): string | null {
  const now = Date.now();
  const upcoming = kickoffs.map((k) => (k ? new Date(k).getTime() : NaN)).filter((t) => t > now).sort((a, b) => a - b);
  return upcoming.length
    ? new Date(upcoming[0]).toLocaleString(undefined, { weekday: 'short', hour: 'numeric', minute: '2-digit', timeZoneName: 'short' })
    : null;
}

function Checklist({ league }: { league: MyLeague }) {
  const team = useAsync((signal) => getMyTeam(league.id, signal), [league.id]);
  const waivers = useAsync((signal) => getLeagueWaivers(league.id, { limit: 3 }, signal).catch(() => null), [league.id]);
  if (team.status === 'error') {
    return (
      <p className="hint">
        Couldn't load your lineup. <button type="button" className="btn btn--ghost btn--sm" onClick={team.reload}>Try again</button>
      </p>
    );
  }
  if (!team.data) return <Skeleton lines={4} label="Checking your lineup…" />;
  const { lineup, alerts = [], roster } = team.data;
  const tossUps = lineup.changes.filter((c) => c.confidence === 'toss_up').length;
  const claim = waivers.data?.results.find((r) => r.advice && ['bid', 'claim', 'add'].includes(r.advice.action));
  const lock = nextLock(roster.filter((r) => r.starting && !r.locked).map((r) => r.kickoff));
  return (
    <ul className="checklist">
      <li>
        {lineup.changes.length === 0 ? 'Lineup is set.' : (
          <>{lineup.changes.length} lineup change{lineup.changes.length === 1 ? '' : 's'} (+{formatNumber(lineup.best_total - lineup.current_total)} pts)
            {tossUps > 0 && <>, {tossUps} toss-up{tossUps === 1 ? '' : 's'}</>}.</>
        )}
      </li>
      {alerts.map((alert) => <li key={alert} className="checklist__warn"><span className="sr-only">Warning: </span>{alert}</li>)}
      {claim && (
        <li>
          Waivers: {claim.advice?.action === 'bid' ? `bid $${claim.advice.bid} on` : 'add'} {claim.player.name}
          {' '}(+{formatNumber(claim.lineup_gain ?? 0)} pts{claim.drop ? `, drop ${claim.drop.name}` : ''}).
        </li>
      )}
      {lock && <li>Next lock: {lock}.</li>}
    </ul>
  );
}

/** "This week" for your Yahoo team: lineup changes and toss-ups, game-day warnings, the best pickup, the next lock. */
export function ThisWeekCard() {
  const { leagues, league } = useMyLeague();
  if (!league || !(leagues.data?.leagues.length)) return null;
  return (
    <Card title="This week" eyebrow={[league.name, league.team].filter(Boolean).join(' · ')}
      actions={<a className="btn btn--ghost btn--sm" href={buildHash(paths.team)}>Open My Team</a>}>
      <Checklist league={league} />
    </Card>
  );
}
