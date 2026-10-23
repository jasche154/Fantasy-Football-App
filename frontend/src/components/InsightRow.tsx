import type { Insight } from '../api/types';
import { formatNumber, formatSigned } from '../lib/format';
import { PlayerLink } from './PlayerLink';

export type InsightKindLabel = 'buy' | 'sell';

interface InsightRowProps {
  insight: Insight;
  kind: InsightKindLabel;
  /** Compact rows (dashboard) show only the first reason. */
  isCompact?: boolean;
}

function signedTone(value: number | null): string {
  if (value === null || Math.abs(value) < 0.05) return 'num';
  return value > 0 ? 'num num--pos' : 'num num--neg';
}

function InsightStats({ insight }: { insight: Insight }) {
  return (
    <dl className="insight-stats">
      <div>
        <dt>PPG vs xPPG</dt>
        <dd className="num">
          {formatNumber(insight.ppg)} <span className="muted">vs</span> {formatNumber(insight.expected_ppg)}
        </dd>
      </div>
      <div>
        <dt title="Fantasy points over expected, per game">FPOE/G</dt>
        <dd className={signedTone(insight.fpoe_per_game)}>{formatSigned(insight.fpoe_per_game)}</dd>
      </div>
      <div>
        <dt title="Actual touchdowns minus expected touchdowns">TD diff</dt>
        <dd className={signedTone(insight.td_diff)}>{formatSigned(insight.td_diff)}</dd>
      </div>
      <div>
        <dt title="Points per game over the last 3 games">Last 3</dt>
        <dd className="num">{formatNumber(insight.recent_ppg)}</dd>
      </div>
    </dl>
  );
}

export function InsightRow({ insight, kind, isCompact = false }: InsightRowProps) {
  const reasons = isCompact ? insight.reasons.slice(0, 1) : insight.reasons;
  return (
    <li className={`insight insight--${kind}${isCompact ? ' insight--compact' : ''}`}>
      <div className="insight__head">
        <PlayerLink player={insight.player} />
        <span className="insight__value num" title="Rest-of-season trade value (model)">
          {formatNumber(insight.trade_value, 0)}
          <span className="insight__value-label"> value</span>
        </span>
      </div>
      <InsightStats insight={insight} />
      {reasons.length > 0 && (
        <ul className="insight__reasons">
          {reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
    </li>
  );
}
