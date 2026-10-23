import type { PlayerSeasonSummary } from '../../api/types';
import {
  formatFraction,
  formatInteger,
  formatNumber,
  formatPercentValue,
  formatSigned,
} from '../../lib/format';

export interface StatTile {
  label: string;
  value: string;
  tag: 'Actual' | 'Expected' | 'Actual − expected' | 'Usage';
  tone?: 'pos' | 'neg';
}

function fpoeTone(value: number | null): StatTile['tone'] {
  if (value === null || Math.abs(value) < 0.05) return undefined;
  return value > 0 ? 'pos' : 'neg';
}

/** Core tiles always show; volume tiles only when the player has that kind of volume. */
export function buildStatTiles(summary: PlayerSeasonSummary): StatTile[] {
  const core: StatTile[] = [
    { label: 'Games', value: formatInteger(summary.games), tag: 'Actual' },
    { label: 'PPG', value: formatNumber(summary.ppg), tag: 'Actual' },
    { label: 'xPPG', value: formatNumber(summary.expected_ppg), tag: 'Expected' },
    { label: 'FPOE / game', value: formatSigned(summary.fpoe_per_game), tag: 'Actual − expected', tone: fpoeTone(summary.fpoe_per_game) },
    { label: 'Fantasy points', value: formatNumber(summary.fantasy_points), tag: 'Actual' },
    { label: 'Total TDs', value: formatInteger(summary.total_tds), tag: 'Actual' },
  ];
  const optional: Array<StatTile | null> = [
    summary.passing_yards > 0 ? { label: 'Pass yards', value: formatInteger(summary.passing_yards), tag: 'Actual' } : null,
    summary.carries > 0 ? { label: 'Carries', value: formatInteger(summary.carries), tag: 'Actual' } : null,
    summary.rushing_yards !== 0 ? { label: 'Rush yards', value: formatInteger(summary.rushing_yards), tag: 'Actual' } : null,
    summary.targets > 0 ? { label: 'Targets', value: formatInteger(summary.targets), tag: 'Usage' } : null,
    summary.receptions > 0 ? { label: 'Receptions', value: formatInteger(summary.receptions), tag: 'Actual' } : null,
    summary.receiving_yards !== 0 ? { label: 'Rec yards', value: formatInteger(summary.receiving_yards), tag: 'Actual' } : null,
    summary.target_share !== null ? { label: 'Target share', value: formatFraction(summary.target_share), tag: 'Usage' } : null,
    summary.snap_pct !== null ? { label: 'Snap %', value: formatPercentValue(summary.snap_pct), tag: 'Usage' } : null,
  ];
  return [...core, ...optional.filter((tile): tile is StatTile => tile !== null)];
}

export function StatTiles({ summary }: { summary: PlayerSeasonSummary }) {
  return (
    <dl className="tiles">
      {buildStatTiles(summary).map((tile) => (
        <div key={tile.label} className="tile">
          <dt className="tile__label">
            {tile.label}{' '}
            <span className="tile__tag">{tile.tag}</span>
          </dt>
          <dd className={tile.tone ? `tile__value num num--${tile.tone}` : 'tile__value num'}>{tile.value}</dd>
        </div>
      ))}
    </dl>
  );
}
