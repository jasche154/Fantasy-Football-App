import { useState } from 'react';
import { findTrades } from '../../api/actions';
import { errorMessage } from '../../api/client';
import { Card } from '../../components/Card';
import { SelectField } from '../../components/SelectField';
import { useAction } from '../../hooks/useAction';
import { formatNumber } from '../../lib/format';
import { buildHash, paths } from '../../router/routes';
import { useLeagueSettings } from '../../state/SettingsContext';

const WANT_OPTIONS = [{ value: '', label: 'Any position' }, ...['QB', 'RB', 'WR', 'TE'].map((p) => ({ value: p, label: p }))];
const VERDICT_LABELS: Record<string, string> = { fair: 'Fair', slight_win: 'Slight win for you', slight_loss: 'Slight loss for you' };

/** Offers built around the players in "You give" that the other manager should accept. */
export function TradeFinder({ giveIds }: { giveIds: readonly number[] }) {
  const { settings } = useLeagueSettings();
  const [want, setWant] = useState('');
  const search = useAction(findTrades);
  return (
    <Card title="Trade finder" eyebrow="Projection (model)">
      <p className="field__hint">Finds fair or slight-edge 1-for-1 and 2-for-1 deals for the players in “You give”.</p>
      <div className="inline-form">
        <SelectField label="I want" isInline value={want} options={WANT_OPTIONS} onChange={setWant} />
        <button type="button" className="btn btn--primary btn--sm" disabled={giveIds.length === 0 || search.status === 'pending'}
          onClick={() => void search.run(giveIds, settings, want)}>
          {search.status === 'pending' ? 'Searching…' : 'Find trades'}
        </button>
      </div>
      {search.status === 'error' && <p className="error-text" role="alert">{errorMessage(search.error)}</p>}
      {search.status === 'success' && (
        search.data.results.length === 0 ? <p className="field__hint" role="status">No acceptable offers found.</p> : (
          <ul className="trade-offers" aria-label="Trade offers">
            {search.data.results.map((offer) => (
              <li key={offer.get.id} className="trade-offer">
                <span>Give <strong>{offer.give.map((p) => p.name).join(' + ')}</strong> for{' '}
                  <strong>{offer.get.name}</strong> ({offer.get.position}, {formatNumber(offer.get_ros_ppg)} PPG)</span>
                <span className="status-badge">{VERDICT_LABELS[offer.evaluation.verdict] ?? offer.evaluation.verdict}</span>
                <a className="btn btn--ghost btn--sm"
                  href={buildHash(paths.trade, { give: offer.give.map((p) => p.id).join(','), get: String(offer.get.id) })}>
                  Load in calculator
                </a>
              </li>
            ))}
          </ul>
        )
      )}
    </Card>
  );
}
