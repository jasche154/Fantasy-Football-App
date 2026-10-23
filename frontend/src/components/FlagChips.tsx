import type { PlayerFlag } from '../api/types';

/** Breakout / regression / risk flags as compact chips; the full reason is in the tooltip and for screen readers. */
export function FlagChips({ flags }: { flags: readonly PlayerFlag[] }) {
  if (flags.length === 0) return null;
  return (
    <ul className="flag-chips" aria-label="Flags">
      {flags.map((flag) => (
        <li key={`${flag.key}-${flag.label}`} className={`flag-chip flag-chip--${flag.kind} flag-chip--${flag.direction}`} title={flag.detail}>
          <span aria-hidden="true">{flag.direction === 'up' ? '▲' : '▼'}</span> {flag.label}
          <span className="sr-only">: {flag.detail}</span>
        </li>
      ))}
    </ul>
  );
}

/** "Q · 78%" style badge for a player's own injury designation. */
export function StatusBadge({ status, playProb }: { status: string | null; playProb: number }) {
  if (!status) return null;
  const short = status.split(' ')[0];
  return (
    <span className={`status-badge injury-badge${playProb < 0.6 ? ' injury-badge--high' : ''}`} title={status}>
      {short} · {Math.round(playProb * 100)}%
    </span>
  );
}
