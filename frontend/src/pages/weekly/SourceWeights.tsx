import type { SourceRefreshStatus, WeeklySourceWeight } from '../../api/types';
import { Icon } from '../../components/Icon';
import { formatFraction } from '../../lib/format';

/** Backend source keys used by the refresh report (not the blend keys). */
const REFRESH_LABELS: Readonly<Record<string, string>> = {
  sleeper_status: 'Sleeper injury status',
  vegas_props: 'Vegas player props',
  yahoo_experts: 'Yahoo experts',
  espn: 'ESPN',
};

interface SourceWeightsProps {
  weights: readonly WeeklySourceWeight[];
  learnedThrough?: number | null;
}

/** One stacked bar showing how much each source counts, with coverage underneath. */
export function SourceWeights({ weights, learnedThrough = null }: SourceWeightsProps) {
  return (
    <figure className="source-weights">
      <figcaption className="source-weights__caption">
        How this week’s projection is blended
        {learnedThrough !== null && (
          <span className="source-weights__learned" title="Refit after each completed week on every week so far, shrunk toward the starting weights">
            {' '}· adjusted by results through week {learnedThrough}
          </span>
        )}
      </figcaption>
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
  const refreshing = Object.entries(statuses ?? {}).filter(([, status]) => status.state === 'refreshing');
  if (notes.length === 0 && refreshing.length === 0) return null;
  return (
    <ul className="refresh-notes" aria-label="Source status">
      {refreshing.length > 0 && (
        <li className="refresh-note refresh-note--refreshing" role="status">
          <span>Updating {refreshing.map(([source]) => REFRESH_LABELS[source] ?? source).join(', ')} in the background. The rankings below use the last stored data and will update shortly.</span>
        </li>
      )}
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
