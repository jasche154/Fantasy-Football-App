import { useEffect, useRef, useState } from 'react';
import { getReplayTeam, getReplayTrades } from '../../api/replay';
import type { ReplayOffer, ReplayShop, ReplaySummary, ReplayTrades } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { Card } from '../../components/Card';
import { Skeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { buildHash, parsePositiveInt, paths } from '../../router/routes';
import { useHashLocation } from '../../router/useHashLocation';
import { TeamBadge } from './gameUi';
import { TradeBuilder } from './TradeBuilder';
import { TradeOffers } from './TradeOffers';
import { PlayerNames, useTradeActions } from './TradeParts';
import { ReplayIrBlockedNotice, type ReplayViewProps } from './shared';

const STATUS: Readonly<Record<ReplayOffer['status'], { label: string; tone: string }>> = {
  accepted: { label: 'Accepted', tone: 'ok' },
  declined: { label: 'Declined', tone: 'out' },
  expired: { label: 'Expired', tone: 'missing' },
  pending: { label: 'Pending', tone: 'missing' },
  countered: { label: 'Countered', tone: 'neutral' },
};

/** The CPU's acceptance test in one line (backend/services/replay_trades.py). */
function rulesText(game: ReplaySummary): string {
  const extra = game.settings.difficulty === 'hard' ? ' (on Hard they want about 8% more than they give)' : '';
  return `CPU managers accept a trade that is worth it to them on the 0-1000 trade chart${extra}; each one has its own `
    + 'trading style. A CPU that turns you down may counter, and you can counter its offers. Each CPU team makes at most 3 trades a week.';
}

function closedText(game: ReplaySummary, trades: ReplayTrades): string {
  if (game.phase === 'complete') return 'Trades are closed: the season is over.';
  if (game.phase === 'draft') return 'Trades open once the draft is done.';
  return `The trade deadline has passed: trades closed at week ${trades.deadline_week}'s waivers.`;
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
              <td className="replay-trade__names"><PlayerNames players={offer.give} onOpen={openPlayer} /></td>
              <td>{offer.to_team.name}</td>
              <td className="replay-trade__names"><PlayerNames players={offer.get} onOpen={openPlayer} /></td>
              <td><span className={`status-badge status-badge--${STATUS[offer.status].tone}`}>{STATUS[offer.status].label}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface ShoppingStripProps {
  game: ReplaySummary;
  shops: readonly ReplayShop[];
  openPlayer: (playerId: number) => void;
  onPick: (shop: ReplayShop) => void;
}

/** Players worried CPU managers are shopping to the user this week, each with his message and a link that opens
 * the builder with that manager and player picked (`onPick`; the href is for opening it elsewhere). */
function ShoppingStrip({ game, shops, openPlayer, onPick }: ShoppingStripProps) {
  if (shops.length === 0) return null;
  return (
    <section className="game-shops" aria-labelledby="game-shops-title">
      <h2 id="game-shops-title" className="section-title">Shopping to you</h2>
      <ul className="game-shops__list">
        {shops.map((shop) => (
          <li key={`${shop.team.team_id}-${shop.player.id}`} className="game-shops__item">
            <p className="game-shops__who">
              <TeamBadge team={shop.team} /> is shopping{' '}
              <button type="button" className="replay-player__name" onClick={() => openPlayer(shop.player.id)}>{shop.player.name}</button>
            </p>
            {shop.message && <blockquote className="game-quote" data-team={shop.team.team_id}><p>{shop.message}</p></blockquote>}
            <a className="btn btn--primary btn--sm game-shops__go"
              href={buildHash(paths.play, { game: game.id, view: 'trades', with: shop.team.team_id, want: shop.player.id })}
              onClick={(event) => { event.preventDefault(); onPick(shop); }}>
              Make an offer <span className="sr-only">to {shop.team.name} for {shop.player.name}</span>
            </a>
          </li>
        ))}
      </ul>
    </section>
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
  // The offer being countered, by id: it is looked up among the pending offers, so it ends once that offer is answered.
  // The id also keys the builder, which stays mounted after a declined counter to show why.
  const [counteringId, setCounteringId] = useState<number | null>(null);
  // A shop's "Make an offer": a fresh builder on that manager and player each press (`n` re-keys it).
  const [shopPick, setShopPick] = useState<{ partner: number; want: number; n: number } | null>(null);
  const builderTitleRef = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (counteringId !== null || shopPick !== null) builderTitleRef.current?.focus();
  }, [counteringId, shopPick]);
  const pickShop = (shop: ReplayShop) => {
    setCounteringId(null);
    setShopPick((last) => ({ partner: shop.team.team_id, want: shop.player.id, n: (last?.n ?? 0) + 1 }));
  };
  const builderKey = counteringId ?? (shopPick ? `shop-${shopPick.n}` : `new-${query.with ?? ''}-${query.want ?? ''}`);
  return (
    <>
      <ReplayIrBlockedNotice game={game} />
      <p className="hint">{rulesText(game)}</p>
      {game.rank_source === 'model' && (
        <p className="hint">No expert ranks are loaded for this week: values come from the app's model ranks.</p>
      )}
      <p ref={noticeRef} tabIndex={-1} role="status" className={`replay-trades__notice${noticeTone}`}>{notice?.text}</p>
      <AsyncView state={loaded} loading={<Skeleton lines={6} label="Loading trades…" />} errorTitle="Couldn't load trades">
        {([trades, mine]) => {
          const countering = trades.offers.find((offer) => offer.id === counteringId) ?? null;
          return (
          <>
            {!trades.open && <p className="notice">{closedText(game, trades)}</p>}
            {trades.open && <ShoppingStrip game={game} shops={trades.shops} openPlayer={openPlayer} onPick={pickShop} />}
            <h2 className="section-title">Offers for you</h2>
            <TradeOffers game={game} version={version} offers={trades.offers} roster={mine.results} isOpen={trades.open}
              actions={actions} openPlayer={openPlayer} onCounter={(offer) => setCounteringId(offer.id)} isCountering={countering !== null} />
            {trades.open && (
              <Card title={<span ref={builderTitleRef} tabIndex={-1}>{countering ? `Counter ${countering.from_team.name}'s offer` : 'Propose a trade'}</span>}
                eyebrow={`Open through week ${trades.deadline_week}'s waivers`}>
                <TradeBuilder key={builderKey} game={game} version={version} roster={mine.results} ir={mine.ir}
                  maxPlayers={trades.max_players} counterTo={countering} onCounterDone={() => setCounteringId(null)}
                  initialPartner={shopPick?.partner ?? parsePositiveInt(query.with)}
                  initialWant={shopPick?.want ?? parsePositiveInt(query.want)}
                  actions={actions} openPlayer={openPlayer} />
              </Card>
            )}
            <h2 className="section-title">Trades and offers so far</h2>
            <TradeHistory offers={trades.results} openPlayer={openPlayer} />
          </>
          );
        }}
      </AsyncView>
    </>
  );
}
