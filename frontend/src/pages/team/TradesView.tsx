import { useState } from 'react';
import { getLeagueOverview, getLeagueTrades } from '../../api/data';
import type { LeagueTradesResponse, ManagerTendencies, MyLeague, Position } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { PositionFilter } from '../../components/PositionFilter';
import { EmptyState, TableSkeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { formatNumber } from '../../lib/format';
import { filterToPosition, type PositionFilterValue } from '../../lib/positions';
import { offerHash, offerTitle } from '../trade/TradeFinder';
import { VERDICT_COPY } from '../trade/TradeResultView';
import { TendencyNote } from './LeagueView';

export function deadlineText(date: string | null | undefined, now: number = Date.now()): string | null {
  if (!date) return null;
  const left = new Date(`${date}T23:59:59`).getTime() - now;
  if (Number.isNaN(left)) return null;
  if (left < 0) return 'The trade deadline has passed.';
  const days = Math.ceil(left / 86_400_000);
  return `Trade deadline ${date} (${days} day${days === 1 ? '' : 's'} left).`;
}

type LeagueOffer = LeagueTradesResponse['teams'][number]['offers'][number];

function OfferLine({ offer, leagueId }: { offer: LeagueOffer; leagueId: number }) {
  const verdict = VERDICT_COPY[offer.evaluation.verdict];
  return (
    <li>
      Give {offer.give.map((p) => p.name).join(' + ')} for <strong>{offer.get.name}</strong> ({offer.get.position})
      {offer.your_gain != null && <> · you +{formatNumber(offer.your_gain)} lineup pts</>}
      {offer.their_gain != null && <>, them {offer.their_gain >= 0 ? '+' : ''}{formatNumber(offer.their_gain)}</>}
      {' '}· <span className={`verdict--${verdict.tone}`}>{verdict.label.toLowerCase()} by value</span>
      {offer.helps_both && <>{' '}<span className="status-badge status-badge--ok">Helps both lineups</span></>}
      {offer.fills_their_need.length > 0 && <>{' '}<span className="status-badge status-badge--ok">Fills their {offer.fills_their_need.join('/')} need</span></>}
      {offer.their_drop && <> · they drop {offer.their_drop.name}</>}
      {offer.your_add && <> · you add {offer.your_add.name} with the open spot</>}
      {' '}<a href={offerHash(offer, leagueId)} aria-label={`Open ${offerTitle(offer)} in calculator`}>Open in calculator</a>
    </li>
  );
}

function TradeTeam({ team, habits, leagueId }: {
  team: LeagueTradesResponse['teams'][number];
  habits?: ManagerTendencies | null;
  leagueId: number;
}) {
  return (
    <li className="league-trade">
      <div className="league-trade__head">
        <strong>{team.team}</strong>
        {team.owner && <span className="league-trade__owner">{team.owner}</span>}
        {team.needs.length > 0 && <span className="league-trade__needs">Needs {team.needs.join(', ')}</span>}
        {habits && <TendencyNote habits={habits} />}
      </div>
      <ul className="league-trade__offers">
        {team.offers.map((offer) => (
          <OfferLine key={`${offer.give.map((p) => p.id).join('-')}-${offer.get.id}`} offer={offer} leagueId={leagueId} />
        ))}
      </ul>
    </li>
  );
}

/** Trade offers with each real team, scored by both starting lineups' gains. */
export function TradesView({ league }: { league: MyLeague }) {
  const [filter, setFilter] = useState<PositionFilterValue>('ALL');
  const position = filterToPosition(filter) as Position | undefined;
  const trades = useAsync((signal) => getLeagueTrades(league.id, position, signal), [league.id, position]);
  // Best-effort: tendencies come from Yahoo; without them the offers still show.
  const overview = useAsync((signal) => getLeagueOverview(league.id, signal).catch(() => null), [league.id]);
  const habitsByTeam = new Map((overview.data?.teams ?? []).map((t) => [t.team, t.tendencies]));
  return (
    <>
      <div className="weekly-controls"><PositionFilter value={filter} onChange={setFilter} /></div>
      <AsyncView state={trades} loading={<TableSkeleton rows={6} columns={3} label="Looking at every roster…" />}
        isEmpty={(d) => d.teams.length === 0}
        empty={trades.data?.deadline_passed
          ? <EmptyState title="The trade deadline has passed.">Trades are closed in this league.</EmptyState>
          : <EmptyState title="No fair trades found.">Try another position.</EmptyState>}>
        {(data) => (
          <>
            <p className="hint">
              Each offer improves your starting lineup (your gain) without costing theirs too much (their gain), and is fair by
              expert consensus. Offers that help both lineups rank first; in a 2-for-1 the gains include their drop and the
              free agent you can add. {deadlineText(data.trade_end_date)}
            </p>
            <ol className="league-trades" aria-label={`Trade ideas in ${data.league}`}>
              {data.teams.map((team) => (
                <TradeTeam key={team.team ?? ''} team={team} habits={habitsByTeam.get(team.team ?? '')} leagueId={league.id} />
              ))}
            </ol>
          </>
        )}
      </AsyncView>
    </>
  );
}
