import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { App } from '../../App';
import { gameLogRow, profile } from '../../test/fixtures';
import { json, mockFetch } from '../../test/mockFetch';
import { META_ROUTE, setHash } from '../../test/render';
import { buildChartGeometry, describeChart, niceStep } from './chartGeometry';
import { visibleStatGroups } from './GameLogTable';
import { availableAdvancedGroups } from './PlayerInsights';
import { buildStatTiles } from './StatTiles';

function renderPlayer(hash: string) {
  setHash(hash);
  return render(<App />);
}

describe('PlayerPage', () => {
  it('renders header, tiles, chart, advanced stats, signals, projection and game log', async () => {
    const api = mockFetch([META_ROUTE, { path: '/api/players/1', reply: json(profile()) }]);
    renderPlayer('#/player/1');

    expect(await screen.findByRole('heading', { level: 1, name: 'Bijan Robinson' })).toBeInTheDocument();
    expect(screen.getByText('ATL')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: /Bijan Robinson weekly fantasy points, 2025/ })).toBeInTheDocument();
    expect(screen.getByText('Rush Yards Over Expected Per Att')).toBeInTheDocument();
    expect(screen.getByText('Buy low')).toBeInTheDocument();
    expect(screen.getByText('Usage is rising: +2.1 expected pts/game lately.')).toBeInTheDocument();
    const projection = screen.getByRole('region', { name: 'Rest of season' });
    expect(within(projection).getByText('Projection (model)')).toBeInTheDocument();
    expect(within(projection).getByText('17.5')).toBeInTheDocument();
    expect(within(projection).getByRole('link', { name: 'Trade for Bijan Robinson' })).toHaveAttribute('href', '#/trade?get=1');
    const log = screen.getByRole('table', { name: /Game log/ });
    expect(within(log).getAllByRole('row')).toHaveLength(4);
    expect(within(log).queryByRole('columnheader', { name: /INT/ })).not.toBeInTheDocument();
    expect(api.callsTo('/api/players/1')[0].url.searchParams.get('scoring')).toBe('ppr');
  });

  it('switches seasons through the hash', async () => {
    const user = userEvent.setup();
    const api = mockFetch([
      META_ROUTE,
      {
        path: '/api/players/1',
        reply: ({ url }) => json(profile({ season: Number(url.searchParams.get('season') ?? 2025), projection: null, game_log: [] })),
      },
    ]);
    renderPlayer('#/player/1');
    const select = await screen.findByRole('combobox', { name: 'Season' });
    await screen.findByRole('option', { name: '2023' });
    await user.selectOptions(select, '2024');
    expect(window.location.hash).toBe('#/player/1?season=2024');
    expect(await screen.findByText('No games logged in 2024.')).toBeInTheDocument();
    expect(screen.getByText('No projection available for this player.')).toBeInTheDocument();
    expect(api.callsTo('/api/players/1').at(-1)?.url.searchParams.get('season')).toBe('2024');
  });

  it('shows a not-found state for unknown players', async () => {
    mockFetch([META_ROUTE, { path: '/api/players/77', reply: json({ detail: 'Player 77 not found' }, 404) }]);
    renderPlayer('#/player/77');
    expect(await screen.findByText('Player not found')).toBeInTheDocument();
    expect(screen.getByText('No player with id 77 exists.')).toBeInTheDocument();
  });

  it('offers a retry for other errors', async () => {
    mockFetch([META_ROUTE, { path: '/api/players/5', reply: json({ detail: 'DB locked' }, 500) }]);
    renderPlayer('#/player/5');
    expect(await screen.findByText("Couldn't load this player")).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });
});

describe('player helpers', () => {
  it('builds bar and expected-point geometry, breaking the line on nulls', () => {
    const rows = [gameLogRow(1, { fantasy_points: 20 }), gameLogRow(2, { expected_points: null }), gameLogRow(3, { fantasy_points: -2 })];
    const geometry = buildChartGeometry(rows);
    expect(geometry.bars).toHaveLength(3);
    expect(geometry.points).toHaveLength(2);
    expect(geometry.linePath.match(/M/g)).toHaveLength(2);
    expect(geometry.bars[2].y).toBeCloseTo(geometry.zeroY);
    expect(geometry.ticks[0].value).toBeLessThan(0);
    expect(buildChartGeometry([]).bars).toEqual([]);
  });

  it('picks nice axis steps', () => {
    expect(niceStep(4.3)).toBe(5);
    expect(niceStep(1.7)).toBe(2);
    expect(niceStep(2.2)).toBe(2.5);
    expect(niceStep(0)).toBe(1);
    expect(niceStep(73)).toBe(100);
  });

  it('describes the chart in words', () => {
    expect(describeChart('X', 2025, [])).toBe('No games logged for X in 2025.');
    expect(describeChart('X', 2025, [gameLogRow(4, { fantasy_points: 30 })])).toContain('Best week: week 4 with 30.0 points');
  });

  it('hides all-zero stat groups and lists non-null advanced groups', () => {
    expect(visibleStatGroups([gameLogRow(1)]).map((group) => group.key)).toEqual(['rushing', 'receiving']);
    expect(availableAdvancedGroups(profile().advanced).map((group) => group.label)).toEqual(['Rushing']);
  });

  it('builds tiles for the volume a player actually has', () => {
    const labels = buildStatTiles(profile().summary).map((tile) => tile.label);
    expect(labels).toContain('Target share');
    expect(labels).not.toContain('Pass yards');
    const fpoe = buildStatTiles({ ...profile().summary, fpoe_per_game: -2 }).find((tile) => tile.label === 'FPOE / game');
    expect(fpoe).toMatchObject({ value: '−2.0', tone: 'neg' });
  });
});
