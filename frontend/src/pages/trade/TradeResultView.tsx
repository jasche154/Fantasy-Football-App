import { useId } from 'react';
import type {
  BalanceSuggestion,
  PlayerSummary,
  TradeLineupImpact,
  TradeResult,
  TradeSide,
  TradeSideKey,
  TradeSidePlayer,
  TradeVerdict,
} from '../../api/types';
import { Icon } from '../../components/Icon';
import { PlayerLink } from '../../components/PlayerLink';
import { formatFraction, formatNumber, formatSigned, formatSignedFraction } from '../../lib/format';
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
  'Package discount: each side is sorted by value — the best player counts 100%, the next 70%, then 50%, and every player after that 40% — so two mid players don’t automatically equal one stud.';

/** "WR12 · 9 games · 60% to play": why a player is worth what he is. */
export function playerContext(entry: TradeSidePlayer): string {
  const parts = [entry.pos_rank, `${formatNumber(entry.ros_ppg, 1)} PPG`];
  if (entry.games_left != null) parts.push(`${entry.games_left} game${entry.games_left === 1 ? '' : 's'} left`);
  const share = entry.availability;
  const reduced = share != null && share < 0.95;
  if (entry.injury_note) {
    parts.push(entry.injury_note);
    if (reduced && share > 0) parts.push(`worth about ${formatFraction(share, 0)} of healthy`);
  } else if (reduced) {
    parts.push(`plays about ${formatFraction(share, 0)}`);
  }
  if (entry.fill_in) parts.push(`may share work with ${entry.fill_in}, who played well while he was out`);
  if (entry.schedule_factor != null && Math.abs(entry.schedule_factor - 1) >= 0.05) {
    parts.push(entry.schedule_factor > 1 ? 'easy schedule' : 'hard schedule');
  }
  return parts.filter(Boolean).join(' · ');
}

/** How expert consensus (the market most managers trade by) sees the same deal. */
export function marketText(market: number | null | undefined): string | null {
  if (market == null) return null;
  if (Math.abs(market) <= 0.05) return 'Expert consensus also sees this as about even.';
  const side = market > 0 ? 'you' : 'the other side';
  return `By expert consensus it favours ${side} by ${formatFraction(Math.abs(market), 0)}.`;
}

export function LineupImpact({ lineup }: { lineup: TradeLineupImpact }) {
  if (lineup.your_gain == null) return <p className="meta-line">{lineup.note ?? 'Lineup gains aren’t available for this trade.'}</p>;
  const helpsBoth = lineup.your_gain > 0 && (lineup.their_gain ?? 0) > 0;
  return (
    <p className="meta-line">
      On your league: your starting lineup {formatSigned(lineup.your_gain)} pts rest of season
      {lineup.their_gain != null && <>, {lineup.their_team ?? 'theirs'} {formatSigned(lineup.their_gain)}</>}.
      {helpsBoth && <> Both lineups improve.</>}
      {lineup.their_drop && <> They drop {lineup.their_drop.name} to make room.</>}
      {lineup.your_add && <> You add {lineup.your_add.name} with the open spot.</>}
      {lineup.your_drop && <> You drop {lineup.your_drop.name} to make room.</>}
    </p>
  );
}

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
                <td className="td--player">
                  <PlayerLink player={entry.player} />
                  <span className="breakdown__context">{playerContext(entry)}</span>
                </td>
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
        Difference: <span className="num">{formatNumber(result.difference, 1)}</span> adjusted points
        (about <span className="num">{formatNumber(result.difference / Math.max(result.weeks_remaining, 1), 1)}</span> a week).
        {' '}{marketText(result.market_percent_difference)}
      </p>
      {result.lineup && <LineupImpact lineup={result.lineup} />}
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
