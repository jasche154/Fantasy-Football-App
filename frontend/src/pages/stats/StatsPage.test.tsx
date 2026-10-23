import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { MetricCategory } from '../../api/types';
import { BIJAN, CHASE } from '../../test/fixtures';
import { json, mockFetch } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { INITIAL_SELECTION, parseMinVolume, resolveMetric, withCategory, withMetric } from './leaderboardSelection';
import { StatsPage } from './StatsPage';

const CATEGORIES: MetricCategory[] = [
  {
    key: 'rushing',
    label: 'Rushing',
    metrics: [
      { key: 'ryoe_per_att', label: 'RYOE / Att', description: 'Rush yards over expected per attempt.', volume_label: 'Attempts', format: 'decimal', higher_is_better: true },
      { key: 'stuffed_pct', label: 'Stuffed %', description: 'Share of runs stopped at or behind the line.', volume_label: 'Attempts', format: 'percent', higher_is_better: false },
    ],
  },
  {
    key: 'receiving',
    label: 'Receiving',
    metrics: [
      { key: 'targets', label: 'Targets', description: 'Total targets.', volume_label: 'Routes', format: 'integer', higher_is_better: true },
    ],
  },
  { key: 'empty', label: 'Empty', metrics: [] },
];

function leaderboard(metric: string, label: string) {
  return {
    category: 'rushing',
    metric,
    label,
    season: 2025,
    min_volume: 50,
    results: [
      { rank: 1, player: BIJAN, value: metric === 'stuffed_pct' ? 62.5 : 1.054, volume: 140 },
      { rank: 2, player: CHASE, value: 0.5, volume: 60 },
    ],
  };
}

function setup() {
  return mockFetch([
    META_ROUTE,
    { path: '/api/leaderboards/metrics', reply: json({ categories: CATEGORIES }) },
    {
      path: '/api/leaderboards',
      reply: ({ url }) => {
        const metric = url.searchParams.get('metric') ?? '';
        return json(leaderboard(metric, metric === 'stuffed_pct' ? 'Stuffed %' : 'RYOE / Att'));
      },
    },
  ]);
}

describe('StatsPage', () => {
  it('populates selectors from the catalog and shows the metric description', async () => {
    const api = setup();
    renderWithProviders(<StatsPage />);

    expect(await screen.findByText('Rush yards over expected per attempt.')).toBeInTheDocument();
    expect(await screen.findByText('1.05')).toBeInTheDocument();
    expect(screen.getByText('2025 season · minimum 50 attempts')).toBeInTheDocument();
    const first = api.callsTo('/api/leaderboards')[0].url.searchParams;
    expect(first.get('category')).toBe('rushing');
    expect(first.get('ascending')).toBe('false');
    expect(await screen.findByRole('option', { name: '2023' })).toBeInTheDocument();
  });

  it('switches metrics (percent values are 0-100) and applies filters', async () => {
    const user = userEvent.setup();
    const api = setup();
    renderWithProviders(<StatsPage />);
    const metricSelect = await screen.findByRole('combobox', { name: 'Metric' });

    await user.selectOptions(metricSelect, 'stuffed_pct');
    expect(await screen.findByText('62.5%')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Ascending (lowest first)' })).toBeChecked();

    await user.selectOptions(screen.getByRole('combobox', { name: 'Position' }), 'RB');
    await user.selectOptions(await screen.findByRole('combobox', { name: 'Season' }), '2024');
    await user.selectOptions(screen.getByRole('combobox', { name: 'Show' }), '50');
    await user.type(screen.getByRole('spinbutton', { name: 'Min attempts' }), '80');

    await screen.findByText('Stuffed %', { selector: '.th-label' });
    await waitFor(() => {
      const last = api.callsTo('/api/leaderboards').at(-1)?.url.searchParams;
      expect(last?.get('min_volume')).toBe('80');
      expect(last?.get('position')).toBe('RB');
      expect(last?.get('season')).toBe('2024');
      expect(last?.get('limit')).toBe('50');
      expect(last?.get('ascending')).toBe('true');
    });

    await user.selectOptions(screen.getByRole('combobox', { name: 'Category' }), 'receiving');
    expect(screen.getByText('Total targets.')).toBeInTheDocument();
  });

  it('validates the minimum volume', async () => {
    const user = userEvent.setup();
    setup();
    renderWithProviders(<StatsPage />);
    const input = await screen.findByRole('spinbutton', { name: 'Min attempts' });
    await user.type(input, '-5');
    expect(screen.getByText(/Enter a number of 0 or more/)).toBeInTheDocument();
    expect(input).toHaveAttribute('aria-invalid', 'true');
  });
});

describe('leaderboard selection helpers', () => {
  it('falls back to the first usable category and metric', () => {
    expect(resolveMetric(CATEGORIES, { categoryKey: 'nope', metricKey: 'nope' })?.metric.key).toBe('ryoe_per_att');
    expect(resolveMetric([], INITIAL_SELECTION)).toBeNull();
  });

  it('defaults lower-is-better metrics to ascending', () => {
    const rushing = withCategory(INITIAL_SELECTION, CATEGORIES, 'rushing');
    expect(rushing.ascending).toBe(false);
    expect(withMetric(rushing, CATEGORIES, 'stuffed_pct').ascending).toBe(true);
    expect(withMetric(rushing, [], 'x')).toBe(rushing);
    expect(withCategory(rushing, [], 'x')).toBe(rushing);
  });

  it('parses the minimum volume input', () => {
    expect(parseMinVolume('')).toEqual({ ok: true, value: undefined });
    expect(parseMinVolume('25')).toEqual({ ok: true, value: 25 });
    expect(parseMinVolume('abc').ok).toBe(false);
  });
});
