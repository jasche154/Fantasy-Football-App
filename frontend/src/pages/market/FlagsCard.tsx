import { getFlags } from '../../api/data';
import type { Position } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { Card } from '../../components/Card';
import { FlagChips } from '../../components/FlagChips';
import { PlayerLink } from '../../components/PlayerLink';
import { EmptyState, Skeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { useLeagueSettings } from '../../state/SettingsContext';

const SHOWN = 12;

/** This week's breakout and regression flags, including injury-driven opportunity. */
export function FlagsCard({ kind, position }: { kind: 'breakout' | 'regression'; position?: Position }) {
  const { settings } = useLeagueSettings();
  const flags = useAsync((signal) => getFlags(settings, { kind, position, limit: SHOWN }, signal), [kind, position, settings]);
  const title = kind === 'breakout' ? 'Breakout watch' : 'Regression watch';
  return (
    <Card title={title} eyebrow="This week · injury-aware">
      <AsyncView state={flags} loading={<Skeleton lines={4} label={`Loading ${title.toLowerCase()}…`} />}
        isEmpty={(d) => d.results.length === 0} empty={<EmptyState title="Nothing flagged this week." />}>
        {(data) => (
          <ul className="flag-list" aria-label={title}>
            {data.results.map((row) => (
              <li key={row.player.id} className="flag-list__row">
                <PlayerLink player={row.player} />
                <FlagChips flags={row.flags} />
                <ul className="reason-list">{row.flags.map((f) => <li key={f.label}>{f.detail}</li>)}</ul>
              </li>
            ))}
          </ul>
        )}
      </AsyncView>
    </Card>
  );
}
