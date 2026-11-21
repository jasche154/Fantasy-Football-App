import { useEffect, useRef, useState } from 'react';
import { getReplayTeam, getReplayTrades } from '../../api/replay';
import type { ReplayOffer, ReplayPendingOffer, ReplaySummary, ReplayTrades } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { Card } from '../../components/Card';
import { Skeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { parsePositiveInt } from '../../router/routes';
import { useHashLocation } from '../../router/useHashLocation';
import { TradeBuilder } from './TradeBuilder';
import { TradeOffers } from './TradeOffers';
import { PlayerNames, useTradeActions } from './TradeParts';
import { ReplayIrBlockedNotice, type ReplayViewProps } from './shared';

const STATUS: Readonly<Record<ReplayOffer['status'], { label: string; tone: string }>> = {
  accepted: { label: 'Accepted', tone: 'ok' },
  declined: { label: 'Declined', tone: 'out' },
  expired: { label: 'Expired', tone: 'missing' },
  pending: { label: 'Pending', tone: 'missing' },
  countered: { label: 'Countered', tone: 'missing' },
};

/** The CPU's acceptance test in one line (backend/services/replay_trades.py). */
function rulesText(game: ReplaySummary): string {
  const extra = game.settings.difficulty === 'hard' ? ' (on Hard they want about 8% more than they give)' : '';
  return `CPU managers accept a trade that is worth it to them on the 0-1000 trade chart${extra}; each one has its own `
    + 'trading style. A CPU that turns you down may counter, and you can counter its offers. Each CPU team trades at most once a week.';
}

function closedText(game: ReplaySummary, trades: ReplayTrades): string {
  return game.phase !== 'complete'
    ? `The trade deadline has passed: trades closed at week ${trades.deadline_week}'s waivers.`
    : 'Trades are closed: the season is over.';
}

/** Every other offer and trade in the league, newest first. */
function TradeHistory({ offers, openPlayer }: { offers: readonly ReplayOffer[]; openPlayer: (playerId: number) => void }) {
  if (offers.length === 0) return <p className="hint">No trades or offers yet.</p>;
  return (
    <div className="table-wrap">
      <table className="data-table data-table--compact">
        <caption className="sr-only">Trades and offers so far</caption>
        <thead>
          <tr>
            <th scope="col" className="num">Week</th>
            <th scope="col">From</th>
            <th scope="col">Sends</th>
            <th scope="col">To</th>
            <th scope="col">For</th>
            <th scope="col">Status</th>
          </tr>
        </thead>
        <tbody>
          {offers.map((offer) => (
            <tr key={offer.id} className={offer.from_team.is_user || offer.to_team.is_user ? 'row--mine' : undefined}>
              <td className="num">{offer.week}</td>
              <td>{offer.from_team.name}</td>
              <td><PlayerNames players={offer.give} onOpen={openPlayer} /></td>
              <td>{offer.to_team.name}</td>
              <td><PlayerNames players={offer.get} onOpen={openPlayer} /></td>
              <td><span className={`status-badge status-badge--${STATUS[offer.status].tone}`}>{STATUS[offer.status].label}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Trades: CPU offers, the trade builder and the league's trades. */
export function TradesView({ game, version, act, isBusy, openPlayer }: ReplayViewProps) {
  const { query } = useHashLocation();
  const loaded = useAsync(
    (signal) => Promise.all([getReplayTrades(game.id, signal), getReplayTeam(game.id, { team_id: game.user_team_id }, signal)]),
    [game.id, game.user_team_id, version],
  );
  // A list still reloading can show an offer that was just answered: keep every button off until it arrives.
  const actions = useTradeActions(act, isBusy || loaded.status === 'loading');
  const { notice } = actions;
  const noticeRef = useRef<HTMLParagraphElement>(null);
  // The answered offer's card (and its focused button) unmount on reload: move focus to the outcome.
  useEffect(() => {
    if (notice) noticeRef.current?.focus();
  }, [notice]);
  const noticeTone = notice ? (notice.isGood ? ' success-text' : ' notice') : '';
  const [countering, setCountering] = useState<ReplayPendingOffer | null>(null);
  return (
    <>
      <ReplayIrBlockedNotice game={game} />
      <p className="hint">{rulesText(game)}</p>
      {game.rank_source === 'model' && (
        <p className="hint">No expert ranks are loaded for this week: values come from the app's model ranks.</p>
      )}
      <p ref={noticeRef} tabIndex={-1} role="status" className={`replay-trades__notice${noticeTone}`}>{notice?.text}</p>
      <AsyncView state={loaded} loading={<Skeleton lines={6} label="Loading trades…" />} errorTitle="Couldn't load trades">
        {([trades, mine]) => (
          <>
            {!trades.open && <p className="notice">{closedText(game, trades)}</p>}
            <h2 className="section-title">Offers for you</h2>
            <TradeOffers game={game} version={version} offers={trades.offers} roster={mine.results} isOpen={trades.open}
              actions={actions} openPlayer={openPlayer} onCounter={setCountering} />
            {trades.open && (
              <Card title={countering ? `Counter ${countering.from_team.name}'s offer` : 'Propose a trade'}
                eyebrow={`Open through week ${trades.deadline_week}'s waivers`}>
                <TradeBuilder key={countering?.id ?? 'new'} game={game} version={version} roster={mine.results}
                  maxPlayers={trades.max_players} counterTo={countering} onCounterDone={() => setCountering(null)}
                  initialPartner={parsePositiveInt(query.with)} initialWant={parsePositiveInt(query.want)}
                  actions={actions} openPlayer={openPlayer} />
              </Card>
            )}
            <h2 className="section-title">Trades and offers so far</h2>
            <TradeHistory offers={trades.results} openPlayer={openPlayer} />
          </>
        )}
      </AsyncView>
    </>
  );
}
