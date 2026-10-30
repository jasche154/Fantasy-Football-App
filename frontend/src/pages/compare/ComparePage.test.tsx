import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { CompareResponse } from '../../api/types';
import { BIJAN, CHASE } from '../../test/fixtures';
import { json, mockFetch } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { buildSeries, ComparePage, timeline } from './ComparePage';

function game(season: number, week: number, points: number) {
  return { season, week, opponent: 'SF', fantasy_points: points, expected_points: points - 1, targets: 5, receptions: 4,
    carries: 10, scrimmage_yards: 80, passing_yards: 0, touchdowns: 1, snap_pct: 70 };
}

function summary(season: number, games: number, ppg: number) {
  return { season, games, ppg, expected_ppg: ppg - 1, best: ppg, worst: ppg, targets_per_game: 5, carries_per_game: 10 };
}

const DATA: CompareResponse = {
  seasons: [2025, 2026],
  players: [
    { player: BIJAN, games: [game(2025, 17, 20), game(2026, 1, 10), game(2026, 2, 30)],
      summary: [summary(2025, 1, 20), summary(2026, 2, 20)] },
    { player: CHASE, games: [game(2026, 2, 12)], summary: [summary(2026, 1, 12)] },
  ],
};

describe('ComparePage', () => {
  it('puts every season on one axis and can smooth with a rolling average', () => {
    expect(timeline(DATA)).toEqual(['2025-17', '2026-01', '2026-02']);
    const raw = buildSeries(DATA, 'fantasy_points', false).series;
    expect(raw[0].points.map((p) => [p.x, p.y])).toEqual([[0, 20], [1, 10], [2, 30]]);
    expect(raw[1].points.map((p) => [p.x, p.y])).toEqual([[2, 12]]);
    const smooth = buildSeries(DATA, 'fantasy_points', true).series;
    expect(smooth[0].points.map((p) => p.y)).toEqual([20, 15, 20]);
  });

  it('loads players from the link and shows the chart and season summaries', async () => {
    const api = mockFetch([META_ROUTE, { path: '/api/compare', reply: json(DATA) }]);
    renderWithProviders(<ComparePage query={{ ids: `${BIJAN.id},${CHASE.id}`, seasons: '2025,2026' }} />);
    const table = await screen.findByRole('table', { name: 'Season summaries' });
    expect(within(table).getAllByRole('row')).toHaveLength(4);
    expect(screen.getByRole('img', { name: /Fantasy points by game/ })).toBeInTheDocument();
    expect(api.callsTo('/api/compare')[0].url.searchParams.get('ids')).toBe(`${BIJAN.id},${CHASE.id}`);

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: `Remove ${CHASE.name}` }));
    expect(screen.queryByRole('button', { name: `Remove ${CHASE.name}` })).not.toBeInTheDocument();
  });

  it('asks for players when none are chosen', () => {
    mockFetch([META_ROUTE]);
    renderWithProviders(<ComparePage query={{}} />);
    expect(screen.getByText('Add players to compare.')).toBeInTheDocument();
  });
});
