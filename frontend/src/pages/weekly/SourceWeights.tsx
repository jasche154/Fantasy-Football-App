import type { SourceRefreshStatus, WeeklySourceWeight } from '../../api/types';
import { Icon } from '../../components/Icon';
import { formatFraction } from '../../lib/format';

/** Backend source keys used by the refresh report (not the blend keys). */
const REFRESH_LABELS: Readonly<Record<string, string>> = {
  vegas_props: 'Vegas player props',
  yahoo_experts: 'Yahoo experts',
  espn: 'ESPN',
};

interface SourceWeightsProps {
  weights: readonly WeeklySourceWeight[];
}

/** One stacked bar showing how much each source counts, with coverage underneath. */
export function SourceWeights({ weights }: SourceWeightsProps) {
  return (
    <figure className="source-weights">
      <figcaption className="source-weights__caption">How this week’s projection is blended</figcaption>
      <div className="source-weights__bar" aria-hidden="true">
        {weights.map((source) => (
          <span
            key={source.key}
            className={`source-weights__segment source--${source.key}${source.players === 0 ? ' is-empty' : ''}`}
            style={{ flexGrow: source.weight }}
          />
        ))}
      </div>
      <ul className="source-weights__legend">
        {weights.map((source) => (
          <li key={source.key} className={source.players === 0 ? 'is-empty' : undefined}>
            <span className={`source-dot source--${source.key}`} aria-hidden="true" />
            <span className="source-weights__label">{source.label}</span>
            <strong>{formatFraction(source.weight, 0)}</strong>
            <span className="source-weights__coverage">
              {source.players === 0 ? 'no data this week' : `${source.players} players`}
            </span>
          </li>
        ))}
      </ul>
    </figure>
  );
}

interface RefreshNotesProps {
  statuses: Readonly<Record<string, SourceRefreshStatus>> | null;
}

/** Only the sources that need attention: not configured, or failed (last snapshot kept). */
export function RefreshNotes({ statuses }: RefreshNotesProps) {
  const notes = Object.entries(statuses ?? {}).filter(
    ([, status]) => status.state === 'error' || status.state === 'not_configured',
  );
  if (notes.length === 0) return null;
  return (
    <ul className="refresh-notes" aria-label="Source status">
      {notes.map(([source, status]) => (
        <li key={source} className={`refresh-note refresh-note--${status.state}`}>
          <Icon name="alert" size={14} />
          <span>
            <strong>{REFRESH_LABELS[source] ?? source}:</strong>{' '}
            {status.state === 'error' ? `${status.message ?? 'refresh failed'} Showing the last stored data.` : status.message}
          </span>
        </li>
      ))}
    </ul>
  );
}
