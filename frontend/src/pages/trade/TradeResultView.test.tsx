import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { LeagueWaiverCandidate } from '../../api/types';
import { BIJAN, KELCE, tradeResult } from '../../test/fixtures';
import { mockFetch } from '../../test/mockFetch';
import { META_ROUTE, renderWithProviders } from '../../test/render';
import { adviceDetail, WaiverRow } from '../waivers/WaiversPage';
import { evenItText, labelTone, TradeResultView } from './TradeResultView';

const noop = () => {};

describe('TradeResultView extras', () => {
  it('shows the consolidation line, what evens it, and the lineup label', () => {
    mockFetch([META_ROUTE]);
    renderWithProviders(
      <TradeResultView
        onAdd={noop}
        result={tradeResult({
          consolidation: { side: 'you', points: 18, note: 'You get the 1-player side of a 2-for-1: extra players count less.' },
          even_it: { add_to: 'get', value: 17.1, closest: { player: KELCE, trade_value: 14 } },
          lineup: { your_gain: 4.2, their_gain: -1.5, their_team: 'Rival', lineup_label: 'Helps you, costs them' },
        })}
      />,
    );
    expect(screen.getByText(/You get the 1-player side of a 2-for-1/)).toBeInTheDocument();
    expect(screen.getByText('To even it: ask for a player worth about 17 (closest: Travis Kelce, value 14).')).toBeInTheDocument();
    expect(screen.getByText('Helps you, costs them')).toHaveClass('status-badge--ok');
  });

  it('leaves the extras out when the API sends none', () => {
    mockFetch([META_ROUTE]);
    renderWithProviders(<TradeResultView onAdd={noop} result={tradeResult({ consolidation: null, even_it: null })} />);
    expect(screen.queryByText(/Consolidation:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/To even it/)).not.toBeInTheDocument();
  });

  it('words the add-to side and colours labels by what they do to your lineup', () => {
    expect(evenItText({ add_to: 'give', value: 9.6, closest: null })).toBe(
      'To even it: add a player to what you give worth about 10.',
    );
    expect(labelTone('Helps both lineups')).toBe('ok');
    expect(labelTone('Hurts both')).toBe('out');
    expect(labelTone('Helps them, even for you')).toBe('missing');
  });
});

describe('waiver advice detail', () => {
  it('shows a FAAB bid with its share of the budget and a claim with the priority it is worth', () => {
    expect(adviceDetail({ action: 'bid', note: '', bid: 14, budget_pct: 14 })).toBe(' $14 · 14% of budget');
    expect(adviceDetail({ action: 'claim', note: '', worth_priority: 4 })).toBe(' · worth #4 priority or later');
    expect(adviceDetail({ action: 'add', note: '' })).toBe('');
  });

  it('renders the bid in a league waiver row', () => {
    mockFetch([META_ROUTE]);
    const row = {
      rank: 1, pos_rank: 'RB1', player: BIJAN, projection: 15, if_active: 15, play_prob: 1, status: null, floor: 4,
      ceiling: 29, boost_points: 0, pct_rostered: 12, scenarios: [], expert_ranks: {}, flags: [], sources: {},
      matchup: { opponent: 'MIA', home: true, sos_factor: 1.1, sos_rank: 6, implied_total: 22, team_ppg: 19, game_final: false },
      ros_value: 40, week_value: 4, schedule_factor: 1, score: 50, faab_pct: 30, faab_label: 'Priority add', reasons: [],
      lineup_gain: 12, drop: null, advice: { action: 'bid', note: 'Bid $14 (14% of budget).', bid: 14, budget_pct: 14 },
    } as unknown as LeagueWaiverCandidate;
    renderWithProviders(<ol><WaiverRow row={row} /></ol>);
    expect(screen.getByText('Bid $14 · 14% of budget')).toBeInTheDocument();
  });
});
