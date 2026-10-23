import type { MetricFormat } from '../api/types';

export const EMPTY_VALUE = '—';
const MINUS_SIGN = '−';

type Numeric = number | null | undefined;

function isNumber(value: Numeric): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function createFormatter(digits: number): Intl.NumberFormat {
  return new Intl.NumberFormat('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

const COMMON_FORMATTERS: readonly Intl.NumberFormat[] = [0, 1, 2, 3].map(createFormatter);

function formatterFor(digits: number): Intl.NumberFormat {
  return COMMON_FORMATTERS[digits] ?? createFormatter(digits);
}

/** Fixed-decimal number with thousands separators; "—" for missing values. */
export function formatNumber(value: Numeric, digits = 1): string {
  if (!isNumber(value)) return EMPTY_VALUE;
  const text = formatterFor(digits).format(Math.abs(value));
  const rounded = Number(value.toFixed(digits));
  return rounded < 0 ? `${MINUS_SIGN}${text}` : text;
}

export function formatInteger(value: Numeric): string {
  return formatNumber(value, 0);
}

/** Always shows a sign: "+1.2", "−0.8", "0.0". */
export function formatSigned(value: Numeric, digits = 1): string {
  if (!isNumber(value)) return EMPTY_VALUE;
  const text = formatNumber(value, digits);
  return Number(value.toFixed(digits)) > 0 ? `+${text}` : text;
}

/** Formats a 0-1 fraction as a percentage ("0.625" -> "62.5%"). */
export function formatFraction(value: Numeric, digits = 1): string {
  if (!isNumber(value)) return EMPTY_VALUE;
  return `${formatNumber(value * 100, digits)}%`;
}

/** Formats a value already on a 0-100 scale ("62.5" -> "62.5%"). */
export function formatPercentValue(value: Numeric, digits = 1): string {
  if (!isNumber(value)) return EMPTY_VALUE;
  return `${formatNumber(value, digits)}%`;
}

/** Signed percentage from a -1..1 fraction ("0.12" -> "+12%"). */
export function formatSignedFraction(value: Numeric, digits = 0): string {
  if (!isNumber(value)) return EMPTY_VALUE;
  return `${formatSigned(value * 100, digits)}%`;
}

/** Leaderboard metric value according to its catalog format. Percent values are 0-100. */
export function formatMetric(value: Numeric, format: MetricFormat): string {
  switch (format) {
    case 'percent':
      return formatPercentValue(value, 1);
    case 'integer':
      return formatInteger(value);
    case 'decimal':
      return formatNumber(value, 2);
  }
}

const ACRONYMS: Readonly<Record<string, string>> = {
  epa: 'EPA',
  cpoe: 'CPOE',
  ryoe: 'RYOE',
  yac: 'YAC',
  xyac: 'xYAC',
  adot: 'aDOT',
  wopr: 'WOPR',
  racr: 'RACR',
  ngs: 'NGS',
  td: 'TD',
  tds: 'TDs',
  int: 'INT',
  ints: 'INTs',
  ppr: 'PPR',
  ppg: 'PPG',
  pct: '%',
  avg: 'Avg',
  att: 'Att',
  qb: 'QB',
  rb: 'RB',
  wr: 'WR',
  te: 'TE',
};

/** "rush_yards_over_expected_per_att" -> "Rush Yards Over Expected Per Att". */
export function humanizeKey(key: string): string {
  return key
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((word) => {
      const lower = word.toLowerCase();
      return ACRONYMS[lower] ?? lower.charAt(0).toUpperCase() + lower.slice(1);
    })
    .join(' ');
}

/** Generic number for advanced-stat values of unknown scale. */
export function formatStat(value: Numeric): string {
  if (!isNumber(value)) return EMPTY_VALUE;
  return Number.isInteger(value) ? formatInteger(value) : formatNumber(value, 2);
}

export function pluralize(count: number, singular: string, plural = `${singular}s`): string {
  return `${formatInteger(count)} ${count === 1 ? singular : plural}`;
}
