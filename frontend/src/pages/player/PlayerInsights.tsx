import type { AdvancedStatGroup, PlayerProfile } from '../../api/types';
import { Card } from '../../components/Card';
import { formatNumber, formatStat, humanizeKey } from '../../lib/format';
import { buildHash, paths } from '../../router/routes';

const ADVANCED_GROUPS = [
  { key: 'rushing', label: 'Rushing' },
  { key: 'receiving', label: 'Receiving' },
  { key: 'passing', label: 'Passing' },
] as const;

export function availableAdvancedGroups(
  advanced: PlayerProfile['advanced'],
): Array<{ key: string; label: string; stats: AdvancedStatGroup }> {
  return ADVANCED_GROUPS.flatMap((group) => {
    const stats = advanced[group.key];
    return stats ? [{ key: group.key, label: group.label, stats }] : [];
  });
}

export function AdvancedStats({ advanced }: { advanced: PlayerProfile['advanced'] }) {
  const groups = availableAdvancedGroups(advanced);
  if (groups.length === 0) {
    return <p className="muted">No advanced (Next Gen Stats) data for this season.</p>;
  }
  return (
    <div className="advanced-grid">
      {groups.map((group) => (
        <Card key={group.key} title={group.label} eyebrow="Advanced · actual" headingLevel={3} className="card--flat">
          <dl className="kv-list">
            {Object.entries(group.stats).map(([key, value]) => (
              <div key={key} className="kv-list__row">
                <dt>{humanizeKey(key)}</dt>
                <dd className="num">{formatStat(value)}</dd>
              </div>
            ))}
          </dl>
        </Card>
      ))}
    </div>
  );
}

export function SignalsPanel({ signals }: { signals: PlayerProfile['signals'] }) {
  const badges = [
    signals.buy_low ? { key: 'buy', label: 'Buy low' } : null,
    signals.sell_high ? { key: 'sell', label: 'Sell high' } : null,
  ].filter((badge): badge is { key: string; label: string } => badge !== null);
  return (
    <Card title="Signals" eyebrow="Actual vs expected" headingLevel={2}>
      {badges.length === 0 ? (
        <p className="muted">No buy-low or sell-high signal for this player right now.</p>
      ) : (
        <p className="signal-badges">
          {badges.map((badge) => (
            <span key={badge.key} className={`signal-badge signal-badge--${badge.key}`}>{badge.label}</span>
          ))}
        </p>
      )}
      {signals.reasons.length > 0 && (
        <ul className="reason-list">
          {signals.reasons.map((reason) => <li key={reason}>{reason}</li>)}
        </ul>
      )}
    </Card>
  );
}

export function ProjectionPanel({ profile }: { profile: PlayerProfile }) {
  const { projection } = profile;
  const tradeHref = buildHash(paths.trade, { get: String(profile.player.id) });
  return (
    <Card title="Rest of season" eyebrow="Projection (model)" headingLevel={2} className="card--projection">
      {projection === null ? (
        <p className="muted">No projection available for this player.</p>
      ) : (
        <dl className="kv-list kv-list--large">
          <div className="kv-list__row"><dt>ROS PPG</dt><dd className="num">{formatNumber(projection.ros_ppg)}</dd></div>
          <div className="kv-list__row"><dt>VORP / game</dt><dd className="num">{formatNumber(projection.vorp_per_game)}</dd></div>
          <div className="kv-list__row"><dt>Trade value</dt><dd className="num">{formatNumber(projection.trade_value, 0)}</dd></div>
          <div className="kv-list__row"><dt>Position rank</dt><dd>{projection.pos_rank ?? '—'}</dd></div>
        </dl>
      )}
      <p className="note">
        Model output, not an expert ranking: blends actual and expected PPG, shrunk toward last season by games played.
      </p>
      <a className="btn btn--ghost btn--sm" href={tradeHref}>Trade for {profile.player.name}</a>
    </Card>
  );
}
