import { useId } from 'react';
import type { BalanceSuggestion, PlayerSummary, TradeResult, TradeSide, TradeSideKey, TradeVerdict } from '../../api/types';
import { Icon } from '../../components/Icon';
import { PlayerLink } from '../../components/PlayerLink';
import { formatFraction, formatNumber, formatSignedFraction } from '../../lib/format';
import { BalanceMeter } from './BalanceMeter';
import { SIDE_TITLES } from './TradeSidePanel';

export const VERDICT_COPY: Readonly<Record<TradeVerdict, { label: string; symbol: string; tone: string }>> = {
  fair: { label: 'Fair trade', symbol: '=', tone: 'even' },
  slight_win: { label: 'Slight win for you', symbol: '▲', tone: 'win' },
  slight_loss: { label: 'Slight loss for you', symbol: '▼', tone: 'loss' },
  big_win: { label: 'Big win for you', symbol: '▲▲', tone: 'win' },
  big_loss: { label: 'Big loss for you', symbol: '▼▼', tone: 'loss' },
};

export const PACKAGE_DISCOUNT_NOTE =
  'Package discount: each side is sorted by value — the best player counts 100%, the next 90%, then 80% and so on (never below 50%) — so two mid players don’t automatically equal one stud.';

function SideBreakdown({ side, data }: { side: TradeSideKey; data: TradeSide }) {
  return (
    <div className="breakdown">
      <h3 className="breakdown__title">{SIDE_TITLES[side]}</h3>
      <div className="table-wrap">
        <table className="data-table data-table--compact">
          <thead>
            <tr>
              <th scope="col" className="th--start">Player</th>
              <th scope="col" className="th--end">Value</th>
              <th scope="col" className="th--end">Weight</th>
              <th scope="col" className="th--end">Adjusted</th>
            </tr>
          </thead>
          <tbody>
            {data.players.map((entry) => (
              <tr key={entry.player.id}>
                <td className="td--player"><PlayerLink player={entry.player} /></td>
                <td className="num">{formatNumber(entry.trade_value, 1)}</td>
                <td className="num">× {formatFraction(entry.weight, 0)}</td>
                <td className="num num--strong">{formatNumber(entry.adjusted_value, 1)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <th scope="row" className="th--start">Total</th>
              <td className="num">{formatNumber(data.raw_total, 1)}</td>
              <td />
              <td className="num num--strong">{formatNumber(data.adjusted_total, 1)}</td>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}

function suggestionLabel(suggestion: BalanceSuggestion): string {
  const target = suggestion.add_to === 'give' ? 'Add to what you give' : 'Ask for';
  return `${target}: ${suggestion.player.name}`;
}

interface SuggestionsProps {
  suggestions: readonly BalanceSuggestion[];
  onAdd: (side: TradeSideKey, player: PlayerSummary) => void;
}

function BalanceSuggestions({ suggestions, onAdd }: SuggestionsProps) {
  const titleId = useId();
  if (suggestions.length === 0) return null;
  return (
    <section className="suggestions" aria-labelledby={titleId}>
      <h3 id={titleId} className="suggestions__title">Balance it</h3>
      <ul className="suggestions__list">
        {suggestions.map((suggestion) => (
          <li key={`${suggestion.add_to}-${suggestion.player.id}`}>
            <button type="button" className="suggestion" onClick={() => onAdd(suggestion.add_to, suggestion.player)}>
              <Icon name="plus" size={16} />
              <span className="suggestion__text">{suggestionLabel(suggestion)}</span>
              <span className="suggestion__meta num">
                {suggestion.player.position ?? '—'} · value {formatNumber(suggestion.trade_value, 0)} · gap → {formatSignedFraction(suggestion.resulting_percent_difference)}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

interface TradeResultViewProps {
  result: TradeResult;
  onAdd: (side: TradeSideKey, player: PlayerSummary) => void;
}

export function TradeResultView({ result, onAdd }: TradeResultViewProps) {
  const verdict = VERDICT_COPY[result.verdict];
  return (
    <div className="trade-result">
      <div className="trade-result__headline">
        <p className={`verdict verdict--${verdict.tone}`}>
          <span className="verdict__symbol" aria-hidden="true">{verdict.symbol}</span>
          {verdict.label}
        </p>
        <p className="trade-result__summary">{result.summary}</p>
      </div>
      <BalanceMeter percentDifference={result.percent_difference} />
      <p className="meta-line">
        Values are rest-of-season fantasy points above replacement (model) over {result.weeks_remaining} remaining weeks.
        Difference: <span className="num">{formatNumber(result.difference, 1)}</span> adjusted points.
      </p>
      <BalanceSuggestions suggestions={result.balance_suggestions} onAdd={onAdd} />
      <details className="breakdowns" open>
        <summary>Per-player breakdown</summary>
        <p className="note">{PACKAGE_DISCOUNT_NOTE}</p>
        <div className="breakdowns__grid">
          <SideBreakdown side="give" data={result.give} />
          <SideBreakdown side="get" data={result.get} />
        </div>
      </details>
    </div>
  );
}
