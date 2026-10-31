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

function OfferRow({ offer, league }: { offer: TradeOffer; league?: number }) {
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
      <a className="btn btn--ghost btn--sm" href={offerHash(offer, league)} aria-label={`Load ${offerTitle(offer)} in calculator`}>
        Load in calculator
      </a>
    </li>
  );
}

/** What the finder's live region announces (one always-mounted region, so screen readers hear changes). */
export function finderAnnouncement(status: string, count: number | undefined, isStale: boolean): string {
  if (isStale) return 'Your trade changed. Search again for new offers.';
  if (status === 'pending') return 'Searching for trades…';
  if (status !== 'success' || count === undefined) return '';
  if (count === 0) return 'No acceptable offers found.';
  return `${count} offer${count === 1 ? '' : 's'} found.`;
}

interface TradeFinderProps {
  giveIds: readonly number[];
  /** The league the calculator is scoring on: loaded offers keep it. */
  league?: number;
}

/** Offers built around the players in "You give" that the other manager should accept. */
export function TradeFinder({ giveIds, league }: TradeFinderProps) {
  const { settings } = useLeagueSettings();
  const [want, setWant] = useState('');
  const search = useAction(findTrades);
  // Results (and errors) belong to the inputs they were searched with; once those change, they are stale.
  const [searchedKey, setSearchedKey] = useState('');
  const inputKey = `${giveIds.join(',')}|${want}|${JSON.stringify(settings)}`;
  const isStale = (search.status === 'success' || search.status === 'error') && searchedKey !== inputKey;
  const results = search.status === 'success' && !isStale ? search.data.results : undefined;
  return (
    <Card title="Trade finder" eyebrow="Projection (model)">
      <p className="field__hint">
        Finds 1-for-1 and 2-for-1 deals for the players in “You give” that are fair by expert consensus, with any edge
        coming from where this app’s projection differs. Best expected edge first. Targets any team, not only your league.
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
      <p className="sr-only" aria-live="polite" aria-atomic="true">
        {finderAnnouncement(search.status, results?.length, isStale)}
      </p>
      {search.status === 'error' && !isStale && <p className="error-text" role="alert">{errorMessage(search.error)}</p>}
      {isStale && <p className="field__hint">Your trade changed. Search again for new offers.</p>}
      {results && results.length === 0 && <p className="field__hint">No acceptable offers found.</p>}
      {results && results.length > 0 && (
        <ul className="trade-offers" aria-label="Trade offers">
          {results.map((offer) => (
            <OfferRow key={`${offer.give.map((p) => p.id).join('-')}-${offer.get.id}`} offer={offer} league={league} />
          ))}
        </ul>
      )}
    </Card>
  );
}
