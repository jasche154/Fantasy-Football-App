import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { MetricCategory } from '../../api/types';
import { placeLabels, pointsToLabel } from '../../components/charts/ScatterChart';
import { linePath } from '../../components/charts/LineChart';
import { median, niceTicks } from '../../lib/chartScale';
import { BIJAN, CHASE, KELCE, profile } from '../../test/fixtures';
import { json, mockFetch } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { chooseChart, PRESETS } from './autoChart';
import { joinScatter } from './chartData';
import { ChartsPage } from './ChartsPage';

const CATALOG: MetricCategory[] = [
  { key: 'usage', label: 'Usage & efficiency', metrics: [
    { key: 'target_share', label: 'Target share', description: '', volume_label: 'Games', format: 'percent', higher_is_better: true },
    { key: 'ppr_points_per_game', label: 'PPR points / game', description: '', volume_label: 'Games', format: 'decimal', higher_is_better: true },
    { key: 'wopr', label: 'WOPR', description: '', volume_label: 'Games', format: 'decimal', higher_is_better: true },
  ] },
  { key: 'rushing', label: 'Rushing (Next Gen Stats)', metrics: [
    { key: 'ryoe_per_att', label: 'RYOE / att', description: '', volume_label: 'Attempts', format: 'decimal', higher_is_better: true },
  ] },
];

function board(metric: string) {
  const value = (base: number) => (metric === 'target_share' ? base / 2 : base);
  return { category: 'usage', metric, label: metric, season: 2026, min_volume: 3,
    results: [
      { rank: 1, player: CHASE, value: value(30), volume: 6 },
      { rank: 2, player: BIJAN, value: value(20), volume: 6 },
      { rank: 3, player: KELCE, value: value(-4), volume: 6 },
    ] };
}

function routes() {
  return [
    META_ROUTE,
    { path: '/api/leaderboards/metrics', reply: json({ categories: CATALOG }) },
    { path: '/api/leaderboards', reply: ({ url }: { url: URL }) => json(board(url.searchParams.get('metric') ?? '')) },
    { path: '/api/players/search', reply: json({ results: [BIJAN] }) },
    { path: '/api/players/1', reply: json(profile()) },
  ];
}

describe('ChartsPage', () => {
  it('auto-builds a scatter from the first preset, then bars, then a weekly trend', async () => {
    const user = userEvent.setup();
    const api = mockFetch(routes());
    renderWithProviders(<ChartsPage />);

    const scatter = await screen.findByRole('region', { name: /Target share vs\. PPR points/ });
    expect(within(scatter).getByText('Auto chart · scatter')).toBeInTheDocument();
    expect(await within(scatter).findByRole('group', { name: /Target share vs\. PPR points/ })).toBeInTheDocument();

    const chasePoint = within(scatter).getByRole('button', { name: /^Ja'Marr Chase: Target share/ });
    await user.hover(chasePoint);
    expect(chasePoint.closest('svg')?.querySelector('.auto-chart__hover text')).toHaveTextContent("Ja'Marr Chase");
    await user.click(chasePoint);
    const card = within(scatter).getByRole('complementary', { name: "Ja'Marr Chase details" });
    expect(within(card).getByRole('link', { name: "Ja'Marr Chase" })).toBeInTheDocument();
    expect(within(card).getAllByText(/#1 of \d+ plotted/)).toHaveLength(2);
    expect(within(card).getByText(/Above the median in both/)).toBeInTheDocument();
    expect(chasePoint).toHaveAttribute('aria-pressed', 'true');
    await user.click(within(card).getByRole('button', { name: 'Close player details' }));
    expect(within(scatter).queryByRole('complementary')).not.toBeInTheDocument();
    const boards = api.callsTo('/api/leaderboards').map((c) => c.url.searchParams);
    expect(boards.map((p) => [p.get('metric'), p.get('position'), p.get('limit')])).toEqual([
      ['target_share', 'WR', '200'], ['ppr_points_per_game', 'WR', '200']]);

    await user.click(screen.getByRole('button', { name: /Opportunity score/ }));
    const bars = await screen.findByRole('region', { name: 'WOPR leaders' });
    expect(within(bars).getByText('Auto chart · ranked bars')).toBeInTheDocument();
    expect(await within(bars).findByText("Ja'Marr Chase")).toBeInTheDocument();
    expect(api.callsTo('/api/leaderboards').at(-1)?.url.searchParams.get('position')).toBe('TE');

    await user.type(screen.getByRole('combobox', { name: 'Add a player to track or highlight' }), 'Bij');
    await user.click(await screen.findByRole('option', { name: /Bijan Robinson/ }));
    expect(screen.getByRole('list', { name: 'Chart players' })).toHaveTextContent('Bijan Robinson');
    await user.selectOptions(screen.getByRole('combobox', { name: 'Season metric' }), '');
    const trend = await screen.findByRole('region', { name: 'Fantasy points by week' });
    expect(within(trend).getByText('Auto chart · trend line')).toBeInTheDocument();
    expect(await within(trend).findByRole('img', { name: /^Fantasy points by week/ })).toBeInTheDocument();
    expect(within(trend).getByRole('button', { name: /Download SVG/ })).toBeEnabled();

    await user.click(screen.getByRole('button', { name: 'Remove Bijan Robinson from the chart' }));
    expect(screen.getByText(/Pick a quick chart, a season metric, or add a player/)).toBeInTheDocument();
  });

  it('shows an error when the catalog fails', async () => {
    mockFetch([META_ROUTE, { path: '/api/leaderboards/metrics', reply: json({ detail: 'down' }, 503) }]);
    renderWithProviders(<ChartsPage />);
    expect(await screen.findByText("Couldn't load the metric catalog")).toBeInTheDocument();
  });
});

describe('chart helpers', () => {
  it('chooses the chart type from the selection', () => {
    const base = { players: [], weeklyStat: 'fantasy_points' as const, position: null };
    expect(chooseChart({ ...base, metrics: ['a:b', 'c:d'] })?.kind).toBe('scatter');
    expect(chooseChart({ ...base, metrics: ['a:b'] })?.kind).toBe('leaders');
    expect(chooseChart({ ...base, players: [BIJAN], metrics: [] })?.kind).toBe('trend');
    expect(chooseChart({ ...base, metrics: [] })).toBeNull();
    expect(PRESETS.every((p) => p.metrics.length >= 1)).toBe(true);
  });

  it('builds nice ticks and medians', () => {
    expect(niceTicks(0, 27)).toEqual([0, 10, 20, 30]);
    expect(niceTicks(-3.2, 4.1)).toEqual([-4, -2, 0, 2, 4]);
    expect(niceTicks(5, 5).length).toBeGreaterThan(1);
    expect([median([3, 1, 2]), median([4, 1, 3, 2]), median([])]).toEqual([2, 2.5, 0]);
  });

  it('joins scatter metrics, labels the best points, and avoids label collisions', () => {
    const joined = joinScatter(board('target_share').results, board('ppr_points_per_game').results.slice(0, 2), new Set([BIJAN.id]));
    expect(joined).toEqual([
      { id: CHASE.id, label: CHASE.name, x: 15, y: 30, isHighlighted: false, player: CHASE },
      { id: BIJAN.id, label: BIJAN.name, x: 10, y: 20, isHighlighted: true, player: BIJAN },
    ]);
    expect([...pointsToLabel(joined, 1)]).toEqual([CHASE.id, BIJAN.id]);
    const placed = placeLabels([
      { id: 1, x: 100, y: 100, text: 'First player' },
      { id: 2, x: 102, y: 102, text: 'Second' },     // collides on the right -> goes left
      { id: 3, x: 101, y: 101, text: 'Third' },      // no room either side -> skipped
    ]);
    expect(placed.map((p) => [p.id, p.anchor])).toEqual([[1, 'start'], [2, 'end']]);
  });

  it('breaks trend lines at missed weeks', () => {
    const identity = (v: number) => v;
    expect(linePath([{ x: 1, y: 5 }, { x: 2, y: 6 }, { x: 4, y: 7 }], identity, identity)).toBe('M1.0,5.0 L2.0,6.0 M4.0,7.0');
  });
});
