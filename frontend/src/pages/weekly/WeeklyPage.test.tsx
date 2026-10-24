import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { ScheduleStrengthResponse, WeeklyPlayer, WeeklyRankingsResponse } from '../../api/types';
import { factorLabel, factorTone, rankTone } from '../../lib/matchup';
import { BIJAN, CHASE } from '../../test/fixtures';
import { json, mockFetch } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { REFRESH_RELOAD_MS, WeeklyPage } from './WeeklyPage';

function weeklyPlayer(rank: number, overrides: Partial<WeeklyPlayer> = {}): WeeklyPlayer {
  return {
    rank,
    pos_rank: 'WR1',
    player: CHASE,
    projection: 18.4,
    if_active: 18.4,
    play_prob: 1,
    status: null,
    floor: 5.2,
    ceiling: 33.8,
    boost_points: 0,
    pct_rostered: 99,
    scenarios: [],
    flags: [],
    matchup: { opponent: 'BAL', home: false, sos_factor: 1.14, sos_rank: 3, implied_total: 24.5, team_ppg: 23, game_final: false },
    sources: {
      vegas_props: { points: 19.2, rank: null },
      josh_hayden: { points: 18.9, rank: 1.5 },
      model: { points: 16.1, rank: null },
    },
    expert_ranks: { 'Hayden Winks': 1, 'Josh Norris': 2 },
    ...overrides,
  };
}

function weekly(week: number, overrides: Partial<WeeklyRankingsResponse> = {}): WeeklyRankingsResponse {
  return {
    season: 2026,
    week,
    scoring: 'ppr',
    weights: [
      { key: 'vegas_props', label: 'Vegas player props', weight: 0.35, players: 1 },
      { key: 'josh_hayden', label: 'Josh & Hayden (Yahoo)', weight: 0.3, players: 1 },
      { key: 'model', label: 'Matchup model', weight: 0.15, players: 2 },
      { key: 'yahoo_consensus', label: 'Yahoo expert consensus', weight: 0.1, players: 0 },
      { key: 'espn', label: 'ESPN', weight: 0.1, players: 0 },
    ],
    sos_tilt: 0.25,
    refresh: {
      yahoo_experts: { state: 'fresh', rows: null, message: null },
      espn: { state: 'error', rows: null, message: 'ESPN projections returned HTTP 503.' },
      vegas_props: { state: 'not_configured', rows: null, message: 'Add ODDS_API_KEY to backend/.env to include Vegas player props.' },
    },
    results: [
      weeklyPlayer(1),
      weeklyPlayer(2, {
        pos_rank: 'RB1', player: BIJAN, projection: 15.2,
        matchup: { opponent: 'SF', home: true, sos_factor: 0.88, sos_rank: 29, implied_total: null, team_ppg: null, game_final: true },
        sources: { model: { points: 15.2, rank: null } }, expert_ranks: {},
      }),
    ],
    ...overrides,
  };
}

const STRENGTH: ScheduleStrengthResponse = {
  season: 2026, week: 7, position: 'WR',
  defenses: [
    { rank: 1, team: 'DET', sos_factor: 1.19, games: 6, allowed_per_game: 44.2, this_week_opponent: 'GB' },
    { rank: 2, team: 'KC', sos_factor: 0.9, games: 6, allowed_per_game: 30.1, this_week_opponent: null },
  ],
  offenses: [{ team: 'GB', this_week: 'DET', next_weeks: [{ opponent: 'DET', sos_factor: 1.19 }, { opponent: 'CHI', sos_factor: 1.0 }],
               rest_of_season: 1.06, games_left: 10 }],
};

function routes() {
  return [
    META_ROUTE,
    { path: '/api/rankings/weekly', reply: ({ url }: { url: URL }) => json(weekly(Number(url.searchParams.get('week') ?? 7))) },
    { method: 'POST' as const, path: '/api/rankings/weekly/refresh', reply: json({ season: 2026, week: 7, sources: {} }) },
    { path: '/api/schedule-strength', reply: json(STRENGTH) },
  ];
}

describe('WeeklyPage', () => {
  it('shows the blend, matchups, every source and source problems', async () => {
    const api = mockFetch(routes());
    renderWithProviders(<WeeklyPage />);

    expect(await screen.findByRole('heading', { level: 1, name: 'Week 7 Rankings' })).toBeInTheDocument();
    expect(api.callsTo('/api/rankings/weekly')[0].url.searchParams.get('scoring')).toBe('ppr');
    expect(screen.getByText('How this week’s projection is blended')).toBeInTheDocument();
    expect(screen.getByText('35%')).toBeInTheDocument();
    expect(screen.getAllByText('no data this week')).toHaveLength(2);

    const table = screen.getByRole('table');
    const [, chaseRow, bijanRow] = within(table).getAllByRole('row');
    expect(within(chaseRow).getByText('@ BAL')).toBeInTheDocument();
    expect(within(chaseRow).getByText('Easy')).toBeInTheDocument();
    expect(within(chaseRow).getByText(/BAL is #3 easiest vs WR: \+14%/)).toBeInTheDocument();
    expect(within(chaseRow).getByText('19.2')).toBeInTheDocument();        // props
    expect(within(chaseRow).getByText('WR1.5')).toBeInTheDocument();       // Josh & Hayden rank
    expect(within(bijanRow).getByText('vs SF')).toBeInTheDocument();
    expect(within(bijanRow).getByText('Tough')).toBeInTheDocument();
    expect(within(bijanRow).getByText('Final')).toBeInTheDocument();

    const notes = screen.getByRole('list', { name: 'Source status' });
    expect(within(notes).getByText(/HTTP 503\. Showing the last stored data\./)).toBeInTheDocument();
    expect(within(notes).getByText(/ODDS_API_KEY/)).toBeInTheDocument();
    expect(within(notes).queryByText(/Yahoo/)).not.toBeInTheDocument();      // fresh sources stay quiet
  });

  it('sorts by any projection column, keeping players a source skips at the bottom', async () => {
    mockFetch(routes());
    const user = userEvent.setup();
    renderWithProviders(<WeeklyPage />);
    const table = await screen.findByRole('table');
    const names = () => within(table).getAllByRole('row').slice(1).map((row) => within(row).getAllByRole('link')[0].textContent);
    expect(names()).toEqual([CHASE.name, BIJAN.name]);

    await user.click(within(table).getByRole('button', { name: /^Proj/ }));
    expect(names()).toEqual([CHASE.name, BIJAN.name]);                    // highest first
    await user.click(within(table).getByRole('button', { name: /^Proj/ }));
    expect(names()).toEqual([BIJAN.name, CHASE.name]);
    expect(within(table).getByRole('columnheader', { name: /^Proj/ })).toHaveAttribute('aria-sort', 'ascending');

    await user.click(within(table).getByRole('button', { name: /Props/ }));
    await user.click(within(table).getByRole('button', { name: /Props/ }));     // ascending: Bijan has no props line
    expect(names()).toEqual([CHASE.name, BIJAN.name]);
  });

  it('changes week and position, and refreshes sources on demand', async () => {
    const user = userEvent.setup();
    const api = mockFetch(routes());
    renderWithProviders(<WeeklyPage />);
    await screen.findByRole('heading', { level: 1, name: 'Week 7 Rankings' });

    await user.selectOptions(screen.getByRole('combobox', { name: 'Week' }), '9');
    expect(await screen.findByRole('heading', { level: 1, name: 'Week 9 Rankings' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'WR' }));
    const last = api.callsTo('/api/rankings/weekly').at(-1);
    expect(last?.url.searchParams.get('week')).toBe('9');
    expect(last?.url.searchParams.get('position')).toBe('WR');

    const before = api.callsTo('/api/rankings/weekly').length;
    await user.click(await screen.findByRole('button', { name: /Refresh sources/ }));
    const refresh = api.callsTo('/api/rankings/weekly/refresh', 'POST');
    expect(refresh[0].url.searchParams.get('week')).toBe('9');
    expect(refresh[0].url.searchParams.get('force')).toBe('true');
    await screen.findByRole('button', { name: /Refresh sources/ });
    expect(api.callsTo('/api/rankings/weekly').length).toBeGreaterThan(before);
  });

  it('switches to schedule strength', async () => {
    const user = userEvent.setup();
    const api = mockFetch(routes());
    renderWithProviders(<WeeklyPage />);
    await screen.findByRole('heading', { level: 1, name: 'Week 7 Rankings' });

    await user.click(screen.getByRole('button', { name: 'Schedule strength' }));
    expect(screen.getByText(/Showing WR\. Pick a position/)).toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: 'Defenses vs WR' })).toBeInTheDocument();
    expect(screen.getByText('vs GB offense')).toBeInTheDocument();
    expect(screen.getByText('Bye')).toBeInTheDocument();
    expect(screen.getAllByText('+19%').length).toBeGreaterThan(0);
    expect(api.callsTo('/api/schedule-strength')[0].url.searchParams.get('week')).toBe('7');
  });

  it('says when sources update in the background and reloads once afterwards', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      let calls = 0;
      mockFetch([META_ROUTE, { path: '/api/rankings/weekly', reply: () => {
        calls += 1;
        return json(weekly(7, { refresh: calls === 1
          ? { sleeper_status: { state: 'refreshing', rows: null, message: null } } : null }));
      } }]);
      renderWithProviders(<WeeklyPage />);
      expect(await screen.findByText(/Updating Sleeper injury status in the background/)).toBeInTheDocument();
      await vi.advanceTimersByTimeAsync(REFRESH_RELOAD_MS + 10);
      await waitFor(() => expect(calls).toBe(2));
      await waitFor(() => expect(screen.queryByText(/in the background/)).not.toBeInTheDocument());
    } finally {
      vi.useRealTimers();
    }
  });

  it('shows an empty state when nothing is projected', async () => {
    mockFetch([META_ROUTE, { path: '/api/rankings/weekly', reply: json(weekly(7, { results: [], refresh: null })) }]);
    renderWithProviders(<WeeklyPage />);
    expect(await screen.findByText('No projections for this week yet.')).toBeInTheDocument();
  });
});

describe('matchup helpers', () => {
  it('labels and grades factors and ranks', () => {
    expect(factorLabel(1.14)).toBe('+14%');
    expect(factorLabel(0.9)).toBe('−10%');
    expect([factorTone(1.05), factorTone(1.01), factorTone(0.9)]).toEqual(['easy', 'neutral', 'tough']);
    expect([rankTone(3), rankTone(16), rankTone(30), rankTone(null)]).toEqual(['easy', 'neutral', 'tough', 'neutral']);
  });
});
