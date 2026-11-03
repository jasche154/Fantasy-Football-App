import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from '../../App';
import type { PercentileStat, PlayerUsageResponse } from '../../api/types';
import { profile } from '../../test/fixtures';
import { json, mockFetch } from '../../test/mockFetch';
import { META_ROUTE, setHash } from '../../test/render';
import { formatChange, percentileTone } from './PlayerUsage';

function stat(overrides: Partial<PercentileStat>): PercentileStat {
  return {
    key: 'ypc', label: 'Yards / carry', value: 4.8, unit: '', percentile: 81, direction: 'higher',
    small_sample: false, sample: 120, min_sample: 40, sample_unit: 'carries', pool_size: 40, ...overrides,
  };
}

function usage(): PlayerUsageResponse {
  return {
    player_id: 1,
    season: 2025,
    percentiles: {
      position: 'RB',
      pool_min_games: 4,
      pool_players: 52,
      stats: [
        stat({}),
        stat({ key: 'fumble_rate', label: 'Fumbles lost / 100 touches', value: 2.5, percentile: 12, direction: 'lower' }),
        stat({ key: 'target_share', label: 'Target share', value: 12.5, unit: '%', percentile: null, small_sample: true, sample: 2, min_sample: 3, sample_unit: 'games' }),
      ],
    },
    changes: {
      recent_games: 3,
      recent_weeks: [4, 5, 6],
      baseline_games: 3,
      note: null,
      metrics: [
        { key: 'snap_share', label: 'Snap share', unit: '%', recent: 78, before: 62, delta: 16, flag: 'up', flag_at: 10 },
        { key: 'carries', label: 'Carries / game', unit: '/g', recent: 14, before: 15, delta: -1, flag: null, flag_at: 4 },
      ],
    },
  };
}

describe('PlayerUsage', () => {
  it('shows percentiles, small samples and what changed on the player page', async () => {
    const api = mockFetch([
      META_ROUTE,
      { path: '/api/players/1', reply: json(profile()) },
      { path: '/api/players/1/usage', reply: json(usage()) },
    ]);
    setHash('#/player/1');
    render(<App />);

    const percentiles = await screen.findByRole('region', { name: 'Position percentiles' });
    expect(await within(percentiles).findByText('Yards / carry')).toBeInTheDocument();
    expect(within(percentiles).getByText('81')).toHaveClass('num--pos');
    expect(within(percentiles).getByText('12')).toHaveClass('num--neg');
    expect(within(percentiles).getByText('(2 of 3 games needed)')).toBeInTheDocument();
    expect(within(percentiles).getByText(/Small sample/)).toBeInTheDocument();
    expect(screen.getByText('Last 3 games vs before')).toBeInTheDocument();
    expect(within(percentiles).getByText(/Percentile among 52 RBs with 4\+ games/)).toBeInTheDocument();

    const changes = screen.getByRole('region', { name: 'What changed' });
    const snap = within(changes).getByRole('row', { name: /Snap share/ });
    expect(within(snap).getByText('62.0%')).toBeInTheDocument();
    expect(within(snap).getByText('+16.0 pts')).toBeInTheDocument();
    expect(within(snap).getByText('Big rise')).toBeInTheDocument();
    expect(within(within(changes).getByRole('row', { name: /Carries/ })).queryByText(/Big/)).not.toBeInTheDocument();
    expect(api.callsTo('/api/players/1/usage')[0].url.searchParams.get('season')).toBe('2025');
  });

  it('explains when there are not enough games to compare', async () => {
    const body = usage();
    mockFetch([
      META_ROUTE,
      { path: '/api/players/1', reply: json(profile()) },
      {
        path: '/api/players/1/usage',
        reply: json({ ...body, changes: { ...body.changes!, metrics: [], note: 'Needs 5+ games this season.' } }),
      },
    ]);
    setHash('#/player/1');
    render(<App />);
    expect(await screen.findByText('Needs 5+ games this season.')).toBeInTheDocument();
  });

  it('tones percentiles by direction and formats changes', () => {
    expect(percentileTone(stat({ percentile: 90 }))).toBe('good');
    expect(percentileTone(stat({ percentile: 20 }))).toBe('bad');
    expect(percentileTone(stat({ percentile: 50 }))).toBe('neutral');
    expect(percentileTone(stat({ percentile: 95, direction: 'context' }))).toBe('neutral');
    expect(percentileTone(stat({ percentile: null }))).toBe('neutral');
    expect(formatChange({ delta: -5.5, unit: '%' })).toBe('−5.5 pts');
    expect(formatChange({ delta: 2, unit: '/g' })).toBe('+2.0/g');
  });
});
