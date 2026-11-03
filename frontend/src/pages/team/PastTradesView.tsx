import type { ManagerTag, TradeGrade, TradeGradeSide } from '../../api/types';
import { formatNumber, formatSigned } from '../../lib/format';

/** Manager tags as chips; hover (or focus) a chip for the numbers behind it. */
export function TagChips({ tags }: { tags: ManagerTag[] | undefined }) {
  if (!tags || tags.length === 0) return null;
  return (
    <ul className="tag-chips" aria-label="Manager tags">
      {tags.map((t) => (
        <li key={t.tag} className="tag-chip" title={t.evidence}>
          {t.tag}<span className="sr-only">: {t.evidence}</span>
        </li>
      ))}
    </ul>
  );
}

function weeksText(weeks: number[]): string {
  if (weeks.length === 0) return 'no weeks played since';
  return weeks.length === 1 ? `week ${weeks[0]}` : `weeks ${weeks[0]}–${weeks[weeks.length - 1]}`;
}

function SideLine({ side, graded }: { side: TradeGradeSide; graded: boolean }) {
  const players = side.received.map((p) => `${p.name} (${formatNumber(p.points)})`).join(', ') || 'no matched players';
  return (
    <li>
      <strong>{side.team}</strong> got {players}
      {graded && <>: <span className={side.net >= 0 ? 'value-pos' : 'value-neg'}>{formatSigned(side.net)} pts</span> since the trade</>}
    </li>
  );
}

/** The season's completed trades, graded on what the players actually scored afterwards. */
export function PastTradesView({ trades }: { trades: TradeGrade[] | undefined }) {
  if (!trades || trades.length === 0) return null;
  return (
    <section aria-label="Past trades" className="past-trades">
      <h3>Past trades</h3>
      <p className="hint">
        Each side's points from the players it received minus the players it sent, in your league's scoring, over the
        weeks played since the trade. A side ahead by 10+ points wins it.
      </p>
      <ul className="past-trade-list">
        {trades.map((trade, index) => (
          <li key={`${trade.date}-${index}`} className="past-trade">
            <p className="past-trade__head">
              {trade.date ?? 'Undated'} · {weeksText(trade.weeks)}
              {trade.graded && (trade.winner
                ? <> · <span className="status-badge status-badge--ok">{trade.winner} won</span></>
                : <> · even so far</>)}
            </p>
            <ul className="past-trade__sides">
              {trade.sides.map((side) => <SideLine key={side.team_key} side={side} graded={trade.graded} />)}
            </ul>
            {trade.unmatched.length > 0 && (
              <p className="past-trade__note">Not counted (no stats match): {trade.unmatched.join(', ')}</p>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
