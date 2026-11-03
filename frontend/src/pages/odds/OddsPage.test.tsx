import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { App } from '../../App';
import type { OddsGame, OddsResponse } from '../../api/types';
import { json, mockFetch } from '../../test/mockFetch';
import { META_ROUTE, setHash } from '../../test/render';
import { propsTimingLabel, spreadLabel, visiblePropColumns } from './OddsGamesTable';
import { weekFromQuery } from './OddsPage';

function game(overrides: Partial<OddsGame> = {}): OddsGame {
  return {
    home_team: 'CIN', away_team: 'BAL', gameday: '2025-11-09', gametime: '13:00', kickoff: '2025-11-09T18:00:00+00:00',
    final: false, home_score: null, away_score: null, spread_line: 3.5, total_line: 47.5,
    home_implied: 25.5, away_implied: 22, props: [], props_fetched_at: null, props_hours_before_kickoff: null,
    ...overrides,
  };
}

const WITH_PROPS = game({
  props: [
    { player_id: 2, name: "Ja'Marr Chase", position: 'WR', team: 'CIN', opponent: 'BAL',
      stats: { rec: 6.5, rec_yds: 81.2, scrimmage_tds: 0.55 }, fetched_at: '2025-11-09T16:30:00+00:00' },
  ],
  props_fetched_at: '2025-11-09T16:30:00+00:00',
  props_hours_before_kickoff: 1.5,
});

function board(overrides: Partial<OddsResponse> = {}): OddsResponse {
  return { season: 2025, week: 10, games: [WITH_PROPS, game({ home_team: 'NYJ', away_team: 'MIA', spread_line: -2 })], props_count: 1, note: null, ...overrides };
}

describe('OddsPage', () => {
  it('lists games with lines and expands a game to its stored props', async () => {
    const user = userEvent.setup();
    const api = mockFetch([META_ROUTE, { path: '/api/odds', reply: json(board()) }]);
    setHash('#/odds');
    render(<App />);

    expect(await screen.findByRole('heading', { level: 1, name: 'Odds & Props' })).toBeInTheDocument();
    const table = await screen.findByRole('table', { name: /Games with the closing spread/ });
    const first = within(table).getByRole('row', { name: /BAL @ CIN/ });
    expect(within(first).getByText('CIN −3.5')).toBeInTheDocument();
    expect(within(first).getByText('25.5')).toBeInTheDocument();
    expect(within(within(table).getByRole('row', { name: /MIA @ NYJ/ })).getByText('None yet')).toBeInTheDocument();
    expect(within(table).getByRole('row', { name: /MIA @ NYJ/ })).toHaveTextContent('MIA −2.0');

    await user.click(within(first).getByRole('button', { name: /Show 1 props/ }));
    const props = screen.getByRole('table', { name: 'Player props, BAL at CIN' });
    expect(within(props).getByRole('link', { name: "Ja'Marr Chase" })).toHaveAttribute('href', '#/player/2');
    expect(within(props).getByText('81.2')).toBeInTheDocument();
    expect(within(props).queryByRole('columnheader', { name: 'Pass yds' })).not.toBeInTheDocument();
    expect(screen.getByText(/Props from 1.5 h before kickoff/)).toBeInTheDocument();
    expect(api.callsTo('/api/odds')[0].url.searchParams.get('week')).toBeNull();
  });

  it('explains the empty props state and switches weeks through the hash', async () => {
    const user = userEvent.setup();
    const note = 'No player props are stored for this week yet. Props are bought once per game within 2 hours of kickoff to save API credits.';
    const api = mockFetch([
      META_ROUTE,
      { path: '/api/odds', reply: ({ url }) => json(board({ week: Number(url.searchParams.get('week') ?? 10), games: [game()], props_count: 0, note })) },
    ]);
    setHash('#/odds');
    render(<App />);
    expect(await screen.findByText(note)).toBeInTheDocument();

    await user.selectOptions(screen.getByRole('combobox', { name: 'Week' }), '9');
    expect(window.location.hash).toBe('#/odds?week=9');
    expect(await screen.findByRole('heading', { name: 'Week 9 games' })).toBeInTheDocument();
    expect(api.callsTo('/api/odds').at(-1)?.url.searchParams.get('week')).toBe('9');
  });

  it('shows the nav entry', async () => {
    mockFetch([META_ROUTE, { path: '/api/odds', reply: json(board()) }]);
    setHash('#/odds');
    render(<App />);
    const links = await screen.findAllByRole('link', { name: /Odds & Props/ });
    expect(links[0]).toHaveAttribute('href', '#/odds');
  });
});

describe('odds helpers', () => {
  it('labels spreads, timing, columns and weeks', () => {
    expect(spreadLabel({ home_team: 'A', away_team: 'B', spread_line: 0 })).toBe('Pick');
    expect(spreadLabel({ home_team: 'A', away_team: 'B', spread_line: null })).toBe('—');
    expect(propsTimingLabel({ props_fetched_at: null, props_hours_before_kickoff: null })).toBeNull();
    expect(visiblePropColumns(WITH_PROPS.props).map((c) => c.key)).toEqual(['rec', 'rec_yds', 'scrimmage_tds']);
    expect(weekFromQuery('9')).toBe(9);
    expect(weekFromQuery('19')).toBeUndefined();
    expect(weekFromQuery('x')).toBeUndefined();
  });
});
