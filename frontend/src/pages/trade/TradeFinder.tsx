import { useState } from 'react';
import { findTrades } from '../../api/actions';
import { errorMessage } from '../../api/client';
import type { TradeOffer } from '../../api/types';
import { Card } from '../../components/Card';
import { SelectField } from '../../components/SelectField';
import { useAction } from '../../hooks/useAction';
import { formatFraction, formatNumber } from '../../lib/format';
import { buildHash, paths } from '../../router/routes';
import { useLeagueSettings } from '../../state/SettingsContext';
import { VERDICT_COPY } from './TradeResultView';

const WANT_OPTIONS = [{ value: '', label: 'Any position' }, ...['QB', 'RB', 'WR', 'TE'].map((p) => ({ value: p, label: p }))];

export function offerTitle(offer: Pick<TradeOffer, 'give' | 'get'>): string {
  return `${offer.give.map((p) => p.name).join(' + ')} for ${offer.get.name}`;
}

/** Calculator link for an offer, keeping the league it was found in. */
export function offerHash(offer: Pick<TradeOffer, 'give' | 'get'>, league?: number): string {
  return buildHash(paths.trade, {
    give: offer.give.map((p) => p.id).join(','),
    get: String(offer.get.id),
    league: league === undefined ? undefined : String(league),
  });
}

function OfferRow({ offer }: { offer: TradeOffer }) {
  const verdict = VERDICT_COPY[offer.evaluation.verdict];
  return (
    <li className="trade-offer">
      <span>Give <strong>{offer.give.map((p) => p.name).join(' + ')}</strong> for{' '}
        <strong>{offer.get.name}</strong> ({offer.get.position}, {formatNumber(offer.get_ros_ppg)} PPG)</span>
      <span className={`status-badge verdict--${verdict.tone}`}>
        <span aria-hidden="true">{verdict.symbol} </span>{verdict.label}
      </span>
      {offer.accept_chance != null && (
        <span className="status-badge" title="Rough chance they accept, from how expert consensus sees the deal for them">
          ~{formatFraction(offer.accept_chance, 0)} to accept
        </span>
      )}
      <a className="btn btn--ghost btn--sm" href={offerHash(offer)} aria-label={`Load ${offerTitle(offer)} in calculator`}>
        Load in calculator
      </a>
    </li>
  );
}

/** Offers built around the players in "You give" that the other manager should accept. */
export function TradeFinder({ giveIds }: { giveIds: readonly number[] }) {
  const { settings } = useLeagueSettings();
  const [want, setWant] = useState('');
  const search = useAction(findTrades);
  // Results belong to the inputs they were searched with; once those change, they are stale.
  const [searchedKey, setSearchedKey] = useState('');
  const inputKey = `${giveIds.join(',')}|${want}|${JSON.stringify(settings)}`;
  const isStale = search.status === 'success' && searchedKey !== inputKey;
  return (
    <Card title="Trade finder" eyebrow="Projection (model)">
      <p className="field__hint">
        Finds 1-for-1 and 2-for-1 deals for the players in “You give” that are fair by expert consensus, with any edge
        coming from where this app’s projection differs. Best expected edge first.
      </p>
      <div className="inline-form">
        <SelectField label="I want" isInline value={want} options={WANT_OPTIONS} onChange={setWant} />
        <button type="button" className="btn btn--primary btn--sm" disabled={giveIds.length === 0 || search.status === 'pending'}
          onClick={() => {
            setSearchedKey(inputKey);
            void search.run(giveIds, settings, want);
          }}>
          {search.status === 'pending' ? 'Searching…' : 'Find trades'}
        </button>
      </div>
      {search.status === 'error' && <p className="error-text" role="alert">{errorMessage(search.error)}</p>}
      {isStale && <p className="field__hint" role="status">Your trade changed. Search again for new offers.</p>}
      {search.status === 'success' && !isStale && (
        <div role="status" aria-live="polite">
          {search.data.results.length === 0 ? <p className="field__hint">No acceptable offers found.</p> : (
            <>
              <p className="sr-only">{search.data.results.length} offers found.</p>
              <ul className="trade-offers" aria-label="Trade offers">
                {search.data.results.map((offer) => (
                  <OfferRow key={`${offer.give.map((p) => p.id).join('-')}-${offer.get.id}`} offer={offer} />
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </Card>
  );
}
