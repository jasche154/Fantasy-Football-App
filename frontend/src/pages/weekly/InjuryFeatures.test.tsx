import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { WeeklyPlayer } from '../../api/types';
import { BIJAN, CHASE, tradeResult } from '../../test/fixtures';
import { json, mockFetch } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { FlagsCard } from '../market/FlagsCard';
import { TradeFinder } from '../trade/TradeFinder';
import { WaiversPage } from '../waivers/WaiversPage';
import { recommend, StartSit } from './StartSit';

function wp(player = BIJAN, overrides: Partial<WeeklyPlayer> = {}): WeeklyPlayer {
  return {
    rank: 1, pos_rank: 'RB1', player, projection: 15, if_active: 15, play_prob: 1, status: null, floor: 4, ceiling: 29,
    boost_points: 0, pct_rostered: 12, scenarios: [], expert_ranks: {}, flags: [], sources: {},
    matchup: { opponent: 'MIA', home: true, sos_factor: 1.1, sos_rank: 6, implied_total: 22, team_ppg: 19, game_final: false },
    ...overrides,
  };
}

const BRAELON = wp({ id: 9, name: 'Braelon Allen', position: 'RB', team: 'NYJ' }, {
  projection: 13.9, floor: 3.9, ceiling: 27,
  scenarios: [{ teammate: 'Breece Hall', teammate_status: 'Questionable (Quadriceps), LP in practice', extra_points: 6.4, projection: 20.3 }],
  flags: [{ key: 'opportunity', kind: 'breakout', direction: 'up', label: '+7.5 if Breece Hall sits', detail: 'Breece Hall is Questionable.' }],
});
const HALL = wp({ id: 8, name: 'Breece Hall', position: 'RB', team: 'NYJ' }, {
  projection: 8.2, if_active: 10.6, play_prob: 0.78, status: 'Questionable (Quadriceps), LP in practice', floor: 0, ceiling: 20.5,
});

describe('recommend', () => {
  it('takes the higher projection unless it is a close call, then floor or ceiling', () => {
    expect(recommend([BRAELON, HALL], 'balanced')?.pick.player.name).toBe('Braelon Allen');
    const close = wp(CHASE, { projection: 13.5, floor: 8, ceiling: 20 });
    expect(recommend([BRAELON, close], 'safe')?.pick.player.name).toBe("Ja'Marr Chase");
    expect(recommend([BRAELON, close], 'upside')?.pick.player.name).toBe('Braelon Allen');
    expect(recommend([BRAELON], 'balanced')).toBeNull();
  });
});

describe('StartSit', () => {
  it('compares players, recommends one, shows injury scenarios and the explanation', async () => {
    const user = userEvent.setup();
    const api = mockFetch([
      META_ROUTE,
      { path: '/api/players/search', reply: ({ url }) => json({ results: url.searchParams.get('q')?.startsWith('Bra')
        ? [BRAELON.player] : [HALL.player] }) },
      { path: '/api/rankings/weekly', reply: ({ url }) => json({ season: 2026, week: 7, scoring: 'ppr', weights: [], sos_tilt: 0.25,
        refresh: null, results: [BRAELON, HALL].filter((p) => url.searchParams.get('ids')?.split(',').includes(String(p.player.id))) }) },
      { path: '/api/rankings/weekly/9/explain', reply: json({ ...BRAELON, season: 2026, week: 7, steps: [
        { label: 'If Breece Hall sits', value: '+7.5 pts', detail: 'Measured from 3 games without him.' }] }) },
    ]);
    renderWithProviders(<StartSit week={7} />);
    expect(screen.getByText('Compare up to four players.')).toBeInTheDocument();
    const box = screen.getByRole('combobox', { name: 'Add a player to compare' });
    await user.type(box, 'Bra');
    await user.click(await screen.findByRole('option', { name: /Braelon Allen/ }));
    await user.type(box, 'Bre');
    await user.click(await screen.findByRole('option', { name: /Breece Hall/ }));

    expect(await screen.findByText('Start Braelon Allen.')).toBeInTheDocument();
    expect(api.callsTo('/api/rankings/weekly').at(-1)?.url.searchParams.get('ids')).toBe('9,8');
    const card = screen.getByRole('article', { name: 'Braelon Allen' });
    expect(within(card).getByText('If Breece Hall sits: 20.3')).toBeInTheDocument();
    expect(within(card).getByText(/\+7\.5 if Breece Hall sits/)).toBeInTheDocument();
    expect(within(screen.getByRole('article', { name: 'Breece Hall' })).getByText('Questionable · 78%')).toBeInTheDocument();
    await user.click(within(card).getByRole('button', { name: 'Why?' }));
    expect(await within(card).findByText('Measured from 3 games without him.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Remove Breece Hall' }));
    expect(screen.queryByText('Start Braelon Allen.')).not.toBeInTheDocument();
  });
});

describe('WaiversPage', () => {
  it('lists adds with bids and refetches by roster rate', async () => {
    const user = userEvent.setup();
    const api = mockFetch([META_ROUTE, { path: '/api/waivers', reply: json({ season: 2026, week: 7, max_rostered: 50, results: [
      { ...BRAELON, ros_value: 40, week_value: 4.1, schedule_factor: 1.06, score: 52, faab_pct: 40, faab_label: 'Priority add',
        reasons: ['Rostered in 12% of ESPN leagues.'] }] }) }]);
    renderWithProviders(<WaiversPage />);
    expect(await screen.findByText('Priority add · bid 40%')).toBeInTheDocument();
    expect(screen.getByText('Rostered in 12% of ESPN leagues.')).toBeInTheDocument();
    await user.selectOptions(screen.getByRole('combobox', { name: 'Rostered' }), '25');
    expect(api.callsTo('/api/waivers').at(-1)?.url.searchParams.get('max_rostered')).toBe('25');
  });
});

describe('TradeFinder', () => {
  it('finds offers for the give side and links them into the calculator', async () => {
    const user = userEvent.setup();
    const api = mockFetch([META_ROUTE, { method: 'POST', path: '/api/trade/find', reply: json({ weeks_remaining: 10, results: [
      { give: [CHASE], get: BIJAN, get_ros_ppg: 19.2, evaluation: tradeResult({ verdict: 'fair' }) }] }) }]);
    renderWithProviders(<TradeFinder giveIds={[CHASE.id]} />);
    await user.selectOptions(screen.getByRole('combobox', { name: 'I want' }), 'RB');
    await user.click(screen.getByRole('button', { name: 'Find trades' }));
    expect(await screen.findByText('Fair')).toBeInTheDocument();
    expect(api.callsTo('/api/trade/find', 'POST')[0].body).toMatchObject({ my_players: [CHASE.id], want_position: 'RB' });
    expect(screen.getByRole('link', { name: 'Load in calculator' })).toHaveAttribute('href', `#/trade?give=${CHASE.id}&get=${BIJAN.id}`);
  });

  it('is disabled with nobody to give', () => {
    mockFetch([META_ROUTE]);
    renderWithProviders(<TradeFinder giveIds={[]} />);
    expect(screen.getByRole('button', { name: 'Find trades' })).toBeDisabled();
  });
});

describe('FlagsCard', () => {
  it('lists flagged players with the reasons', async () => {
    const api = mockFetch([META_ROUTE, { path: '/api/insights/flags', reply: json({ season: 2026, week: 7, results: [BRAELON] }) }]);
    renderWithProviders(<FlagsCard kind="breakout" position="RB" />);
    expect(await screen.findByText('Breece Hall is Questionable.')).toBeInTheDocument();
    const params = api.callsTo('/api/insights/flags')[0].url.searchParams;
    expect([params.get('kind'), params.get('position')]).toEqual(['breakout', 'RB']);
  });
});
