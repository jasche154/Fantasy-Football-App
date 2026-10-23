/** -1..1 (positive favours the user) -> clamped whole percent. */
export function toBalancePercent(percentDifference: number): number {
  if (!Number.isFinite(percentDifference)) return 0;
  return Math.round(Math.max(-1, Math.min(1, percentDifference)) * 100);
}

export function balanceText(percent: number): string {
  if (percent === 0) return 'Dead even';
  return percent > 0 ? `You win by ${Math.abs(percent)}%` : `You lose by ${Math.abs(percent)}%`;
}

/**
 * Tug-of-war bar: the fill grows from the centre toward the side that wins.
 * Left = "You give" side ahead (favours them), right = "You get" side ahead (favours you).
 */
export function BalanceMeter({ percentDifference }: { percentDifference: number }) {
  const percent = toBalancePercent(percentDifference);
  const magnitude = Math.abs(percent) / 2;
  const tone = percent > 0 ? 'win' : percent < 0 ? 'loss' : 'even';
  const text = balanceText(percent);
  return (
    <div className="balance">
      <div
        className={`balance__track balance__track--${tone}`}
        role="meter"
        aria-label="Trade balance"
        aria-valuemin={-100}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-valuetext={text}
      >
        <span className="balance__center" aria-hidden="true" />
        <span
          className="balance__fill"
          aria-hidden="true"
          style={percent >= 0 ? { insetInlineStart: '50%', inlineSize: `${magnitude}%` } : { insetInlineEnd: '50%', inlineSize: `${magnitude}%` }}
        />
      </div>
      <div className="balance__labels" aria-hidden="true">
        <span>◀ Favors them</span>
        <span className={`balance__value balance__value--${tone}`}>{text}</span>
        <span>Favors you ▶</span>
      </div>
    </div>
  );
}
