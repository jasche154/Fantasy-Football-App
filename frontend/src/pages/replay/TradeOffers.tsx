import { useState } from 'react';
import { errorMessage } from '../../api/client';
import { evaluateReplayTrade, respondReplayOffer } from '../../api/replay';
import type { ReplayPendingOffer, ReplayRosterRow, ReplaySummary } from '../../api/types';
import { useAsync } from '../../hooks/useAsync';
import { DropPicker, overLimit, playerIds, TradeSide, TradeVerdictView, type TradeActions } from './TradeParts';

function sameIds(a: readonly number[], b: readonly number[]): boolean {
  return a.length === b.length && a.every((id) => b.includes(id));
}

interface OfferCardProps {
  game: ReplaySummary;
  version: number;
  offer: ReplayPendingOffer;
  /** The user's roster now. */
  roster: readonly ReplayRosterRow[];
  /** False after the trade deadline: an offer can only be declined. */
  isOpen: boolean;
  actions: TradeActions;
  openPlayer: (playerId: number) => void;
  /** Opens the trade builder with this offer's deal, to change and send back. */
  onCounter: (offer: ReplayPendingOffer) => void;
  /** A counter is being built: starting another would throw that deal away. */
  isCountering: boolean;
}

/** A CPU's offer: what goes each way, the preview verdict, the user's drops when it overfills the roster. */
function OfferCard({ game, version, offer, roster, isOpen, actions, openPlayer, onCounter, isCountering }: OfferCardProps) {
  const [picked, setPicked] = useState<number[]>([]);
  const partner = offer.from_team.name;
  const sends = playerIds(offer.get);                      // the offer is written from the CPU's side
  const receives = playerIds(offer.give);
  // A verdict from the server means the deal fits the roster as it is: only an unpriced offer can need drops.
  const over = offer.verdict ? 0 : overLimit(roster, sends.length, receives.length, game.settings.roster_size);
  const spare = roster.filter((row) => !sends.includes(row.player.id));
  const drops = over > 0 ? picked.filter((id) => spare.some((row) => row.player.id === id)) : [];
  const hasDrops = drops.length === Math.max(over, 0);
  // The list can't price an offer that needs drops: ask once the user has chosen them.
  const priced = useAsync(
    over > 0 && hasDrops
      ? (signal) => evaluateReplayTrade(game.id, { to_team: offer.from_team.team_id, give: sends, get: receives, drops }, signal)
      : null,
    [game.id, offer.id, drops.join(','), over, version],
  );
  // Only a verdict for exactly the ticked drops counts: an older one can still be held for a render after the ticks change.
  const pricedVerdict = priced.status === 'success' && sameIds(priced.data.verdict.your_drops.map((row) => row.id), drops)
    ? priced.data.verdict : null;
  const verdict = offer.verdict ?? pricedVerdict;
  const acceptKey = `accept-${offer.id}`;
  const declineKey = `decline-${offer.id}`;
  const error = actions.errorFor(acceptKey) ?? actions.errorFor(declineKey);

  const answer = async (accept: boolean) => {
    const result = await actions.send(accept ? acceptKey : declineKey, () => respondReplayOffer(game.id, offer.id, accept, accept ? drops : []));
    if (!result) return;
    if (result.expired) actions.announce({ text: result.message ?? 'That offer no longer stands.', isGood: false });
    else if (result.accepted) actions.announce({ text: `Trade executed with ${partner}.`, isGood: true });
    // A haggler always comes back after his opening offer is declined: the better one shows under Offers for you.
    else actions.announce({ text: `You declined ${partner}'s offer.${result.counter ? ` ${partner} came back with a better one: see Offers for you.` : ''}`, isGood: false });
  };

  return (
    <li className="league-trade replay-offer" aria-label={`Offer from ${partner}`}>
      <p className="league-trade__head">
        <strong>{partner}</strong> <span className="league-trade__owner">{offer.counter_to === null ? 'offered' : 'countered with'} this in week {offer.week}</span>
      </p>
      {offer.message && (
        <blockquote className="game-quote" data-team={offer.from_team.team_id}>
          <p>{offer.message}</p>
          <footer className="sr-only">{partner}</footer>
        </blockquote>
      )}
      <div className="replay-trade__sides">
        <TradeSide title="You give" players={verdict?.give ?? offer.get.map((player) => ({ player }))} onOpen={openPlayer} />
        <TradeSide title="You get" players={verdict?.get ?? offer.give.map((player) => ({ player }))} onOpen={openPlayer} />
      </div>
      {offer.note && <p className="notice">{offer.note}</p>}
      {over > 0 && <DropPicker rows={spare} count={over} value={drops} onChange={setPicked} />}
      {priced.status === 'loading' && <p className="hint">Checking that choice…</p>}
      {priced.status === 'error' && <p className="error-text" role="alert">{errorMessage(priced.error)}</p>}
      {verdict && <TradeVerdictView verdict={verdict} partner={partner} isOffer />}
      <div className="replay-trade__actions">
        {/* Accepting anything the CPU would refuse only expires the offer, so Accept needs a yes for exactly this choice. */}
        <button type="button" className="btn btn--primary btn--sm" disabled={actions.isBusy || !isOpen || !hasDrops || verdict?.accepted !== true}
          onClick={() => void answer(true)}>
          {actions.pendingKey === acceptKey ? 'Accepting…' : <>Accept <span className="sr-only">offer from {partner}</span></>}
        </button>
        <button type="button" className="btn btn--ghost btn--sm" disabled={actions.isBusy || !isOpen || isCountering} onClick={() => onCounter(offer)}>
          Counter <span className="sr-only">offer from {partner}</span>
        </button>
        <button type="button" className="btn btn--ghost btn--sm" disabled={actions.isBusy} onClick={() => void answer(false)}>
          {actions.pendingKey === declineKey ? 'Declining…' : <>Decline <span className="sr-only">offer from {partner}</span></>}
        </button>
      </div>
      {error && <p className="error-text">{error}</p>}
    </li>
  );
}

interface TradeOffersProps extends Omit<OfferCardProps, 'offer'> {
  offers: readonly ReplayPendingOffer[];
}

/** The CPU offers waiting for the user's answer. */
export function TradeOffers({ offers, ...card }: TradeOffersProps) {
  if (offers.length === 0) return <p className="hint">No offers are waiting for your answer.</p>;
  return (
    <ul className="league-trades">
      {offers.map((offer) => <OfferCard key={offer.id} offer={offer} {...card} />)}
    </ul>
  );
}
