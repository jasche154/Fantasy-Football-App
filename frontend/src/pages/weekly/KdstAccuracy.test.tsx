import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { json, mockFetch } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { AccuracyView } from './AccuracyView';
import { KdstView } from './KdstView';

describe('Kicker / defense views and model accuracy', () => {
  it('lists kickers with attempts, makes and weather', async () => {
    mockFetch([META_ROUTE, { path: '/api/rankings/weekly/kdst', reply: json({ season: 2026, week: 8, position: 'K', results: [
      { rank: 1, player: { id: 5, name: 'Brandon Aubrey', position: 'K', team: 'DAL' }, opponent: 'ARI', projection: 10.24,
        fg_attempts: 2.08, fg_made: 1.8, pat_made: 2.9, wind: 6, temp: 76, indoors: false, reasons: ['Vegas implied total 27.5'] },
    ] }) }]);
    renderWithProviders(<KdstView position="K" week={8} />);
    const row = (await screen.findByText('Brandon Aubrey')).closest('tr') as HTMLElement;
    expect(within(row).getByText('10.2')).toBeInTheDocument();
    expect(row).toHaveTextContent('6 mph, 76°F');
    expect(within(row).getByText('Vegas implied total 27.5')).toBeInTheDocument();
  });

  it('shows live accuracy, held-out backtest and learned weights', async () => {
    mockFetch([META_ROUTE, { path: '/api/model/accuracy', reply: json({
      season: 2026,
      live: { n: 287, weeks: [7], blend_mae: 4.26, bias_by_position: { WR: 0.98 }, range: { n: 263, below_floor: 0.236, above_ceiling: 0.122 } },
      backtest: { seasons: [2021, 2025], held_out: { 2025: { before: { rmse: 7.654, mae: 6.006, rank_corr: 0.502 },
        after: { rmse: 7.448, mae: 5.772, rank_corr: 0.506 } } } },
      weight_history: [{ season: 2026, week: 7, rows: 265, weights: { vegas_props: 0.326 } }],
    }) }]);
    renderWithProviders(<AccuracyView />);
    const live = await screen.findByRole('region', { name: 'This season' });
    expect(live).toHaveTextContent('287 player-weeks');
    expect(live).toHaveTextContent('Below the floor 24%');
    expect(live).toHaveTextContent('Floors have been too high so far');
    expect(live).toHaveTextContent('WR: projected 1.0 too high');
    expect(screen.getByRole('region', { name: 'Backtest' })).toHaveTextContent('7.65 → 7.45');
    expect(screen.getByRole('region', { name: 'Learned weights' })).toHaveTextContent('After week 7 (265 players): vegas props 33%');
  });
});
