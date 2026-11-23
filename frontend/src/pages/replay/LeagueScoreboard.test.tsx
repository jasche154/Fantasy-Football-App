import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { json, mockFetch } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { GAME_ID, side, summary, USER_TEAM_ID, viewProps } from './fixtures';
import { LeagueScoreboard } from './LeagueScoreboard';

const PATH = `/api/replay/games/${GAME_ID}/scoreboard`;

describe('LeagueScoreboard in a league that jumped in', () => {
  it('offers only the league’s own weeks, never one before it began', async () => {
    const api = mockFetch([META_ROUTE, { path: PATH, reply: ({ url }) => json({
      week: Number(url.searchParams.get('week')), played: false, playoff: false,
      results: [{ home: side(USER_TEAM_ID), away: side(1), winner: null, label: null }],
    }) }]);
    renderWithProviders(<LeagueScoreboard {...viewProps({ game: summary({ week: 11, start_week: 10, sim_from: 10, kind: 'live' }) })} />);

    await screen.findByRole('list', { name: 'Week 11 scoreboard' });
    const options = within(screen.getByRole('combobox', { name: 'Week' })).getAllByRole('option');
    expect(options.map((option) => option.textContent)).toEqual(
      ['Week 10', 'Week 11', 'Week 12', 'Week 13', 'Week 14', 'Week 15 (playoffs)', 'Week 16 (playoffs)']);
    expect(api.callsTo(PATH).map((call) => call.url.searchParams.get('week'))).toEqual(['11']);
  });
});
