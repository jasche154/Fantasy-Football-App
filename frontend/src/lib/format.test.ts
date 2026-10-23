import { describe, expect, it } from 'vitest';
import {
  EMPTY_VALUE,
  formatFraction,
  formatInteger,
  formatMetric,
  formatNumber,
  formatPercentValue,
  formatSigned,
  formatSignedFraction,
  formatStat,
  humanizeKey,
  pluralize,
} from './format';

describe('formatNumber', () => {
  it('formats fixed decimals with thousands separators', () => {
    expect(formatNumber(1234.567, 1)).toBe('1,234.6');
    expect(formatNumber(3, 2)).toBe('3.00');
  });

  it('uses a true minus sign for negatives and never shows "-0"', () => {
    expect(formatNumber(-2.5, 1)).toBe('−2.5');
    expect(formatNumber(-0.04, 1)).toBe('0.0');
  });

  it('returns an em dash for missing or non-finite values', () => {
    expect(formatNumber(null)).toBe(EMPTY_VALUE);
    expect(formatNumber(undefined)).toBe(EMPTY_VALUE);
    expect(formatNumber(Number.NaN)).toBe(EMPTY_VALUE);
    expect(formatNumber(Number.POSITIVE_INFINITY)).toBe(EMPTY_VALUE);
  });

  it('supports digit counts outside the cached formatters', () => {
    expect(formatNumber(1.23456, 4)).toBe('1.2346');
  });
});

describe('signed and integer formatting', () => {
  it('prefixes positive values with a plus sign', () => {
    expect(formatSigned(3.94)).toBe('+3.9');
    expect(formatSigned(-3.94)).toBe('−3.9');
    expect(formatSigned(0.01)).toBe('0.0');
    expect(formatSigned(null)).toBe(EMPTY_VALUE);
  });

  it('rounds integers', () => {
    expect(formatInteger(1499.6)).toBe('1,500');
  });
});

describe('percent formatting', () => {
  it('treats fractions (0-1) and percent values (0-100) differently', () => {
    expect(formatFraction(0.625)).toBe('62.5%');
    expect(formatPercentValue(62.5)).toBe('62.5%');
    expect(formatFraction(null)).toBe(EMPTY_VALUE);
    expect(formatPercentValue(undefined)).toBe(EMPTY_VALUE);
  });

  it('formats signed fractions as whole percents', () => {
    expect(formatSignedFraction(0.12)).toBe('+12%');
    expect(formatSignedFraction(-0.3)).toBe('−30%');
    expect(formatSignedFraction(null)).toBe(EMPTY_VALUE);
  });
});

describe('formatMetric', () => {
  it('renders percent metrics as already on a 0-100 scale', () => {
    expect(formatMetric(62.5, 'percent')).toBe('62.5%');
  });

  it('renders decimal and integer metrics', () => {
    expect(formatMetric(1.054, 'decimal')).toBe('1.05');
    expect(formatMetric(312.4, 'integer')).toBe('312');
  });
});

describe('humanizeKey', () => {
  it('title-cases snake_case keys and keeps football acronyms', () => {
    expect(humanizeKey('rush_yards_over_expected_per_att')).toBe('Rush Yards Over Expected Per Att');
    expect(humanizeKey('epa_per_play')).toBe('EPA Per Play');
    expect(humanizeKey('avg_cpoe')).toBe('Avg CPOE');
    expect(humanizeKey('catch_pct')).toBe('Catch %');
  });
});

describe('formatStat and pluralize', () => {
  it('shows integers without decimals and other numbers with two', () => {
    expect(formatStat(12)).toBe('12');
    expect(formatStat(1.256)).toBe('1.26');
    expect(formatStat(null)).toBe(EMPTY_VALUE);
  });

  it('pluralizes by count', () => {
    expect(pluralize(1, 'team')).toBe('1 team');
    expect(pluralize(12, 'team')).toBe('12 teams');
  });
});
