import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { ALLEN, BIJAN, CHASE, KELCE, ranked } from '../../test/fixtures';
import { RankingsTable } from './RankingsTable';
import { defaultRankingDirection, sortRankings } from './rankingsSort';

const ROWS = [
  ranked(1, BIJAN, { ppg: 18.2, ecr: 3, pos_rank: 'RB1' }),
  ranked(2, CHASE, { ppg: 21.5, ecr: null, pos_rank: 'WR1' }),
  ranked(3, ALLEN, { ppg: 24.1, ecr: 1, pos_rank: 'QB1', expected_ppg: null }),
  ranked(4, KELCE, { ppg: 11.0, ecr: 2, pos_rank: 'TE1' }),
];

function playerOrder(): string[] {
  const rows = screen.getAllByRole('row').slice(1);
  return rows.map((row) => within(row).getByRole('link').textContent ?? '');
}

function header(name: RegExp): HTMLElement {
  return screen.getByRole('columnheader', { name });
}

describe('RankingsTable', () => {
  it('starts sorted by rank with labelled provenance columns', () => {
    render(<RankingsTable rows={ROWS} caption="All rankings" />);
    expect(playerOrder()).toEqual(['Bijan Robinson', "Ja'Marr Chase", 'Josh Allen', 'Travis Kelce']);
    expect(header(/^Rank/)).toHaveAttribute('aria-sort', 'ascending');
    expect(header(/^PPG Actual/)).toHaveAttribute('aria-sort', 'none');
    expect(header(/^xPPG Expected/)).toBeInTheDocument();
    expect(header(/^ROS PPG Projection \(blend\)/)).toBeInTheDocument();
  });

  it('sorts by a column on click and toggles direction on a second click', async () => {
    const user = userEvent.setup();
    render(<RankingsTable rows={ROWS} caption="All rankings" />);

    await user.click(screen.getByRole('button', { name: /^PPG Actual/ }));
    expect(playerOrder()).toEqual(['Josh Allen', "Ja'Marr Chase", 'Bijan Robinson', 'Travis Kelce']);
    expect(header(/^PPG Actual/)).toHaveAttribute('aria-sort', 'descending');
    expect(header(/^Rank/)).toHaveAttribute('aria-sort', 'none');

    await user.click(screen.getByRole('button', { name: /^PPG Actual/ }));
    expect(playerOrder()).toEqual(['Travis Kelce', 'Bijan Robinson', "Ja'Marr Chase", 'Josh Allen']);
    expect(header(/^PPG Actual/)).toHaveAttribute('aria-sort', 'ascending');
  });

  it('keeps missing values at the bottom in both directions', async () => {
    const user = userEvent.setup();
    render(<RankingsTable rows={ROWS} caption="All rankings" />);
    await user.click(screen.getByRole('button', { name: /^ECR/ }));
    expect(playerOrder()).toEqual(['Josh Allen', 'Travis Kelce', 'Bijan Robinson', "Ja'Marr Chase"]);
    await user.click(screen.getByRole('button', { name: /^ECR/ }));
    expect(playerOrder().at(-1)).toBe("Ja'Marr Chase");
  });
});

describe('sortRankings', () => {
  it('sorts names alphabetically and positional ranks naturally', () => {
    const rows = [
      ranked(1, BIJAN, { pos_rank: 'RB10' }),
      ranked(2, CHASE, { pos_rank: 'RB2' }),
      ranked(3, ALLEN, { pos_rank: 'QB3' }),
      ranked(4, KELCE, { pos_rank: 'odd' }),
    ];
    expect(sortRankings(rows, { key: 'name', direction: 'asc' }).map((r) => r.player.name)).toEqual([
      'Bijan Robinson',
      "Ja'Marr Chase",
      'Josh Allen',
      'Travis Kelce',
    ]);
    expect(sortRankings(rows, { key: 'pos_rank', direction: 'asc' }).map((r) => r.pos_rank)).toEqual([
      'odd',
      'QB3',
      'RB2',
      'RB10',
    ]);
  });

  it('does not mutate the input and breaks ties by rank', () => {
    const rows = [ranked(2, CHASE, { games: 7 }), ranked(1, BIJAN, { games: 7 })];
    const sorted = sortRankings(rows, { key: 'games', direction: 'desc' });
    expect(sorted.map((r) => r.rank)).toEqual([1, 2]);
    expect(rows.map((r) => r.rank)).toEqual([2, 1]);
  });

  it('defaults rank-like columns to ascending', () => {
    expect(defaultRankingDirection('ecr')).toBe('asc');
    expect(defaultRankingDirection('trade_value')).toBe('desc');
  });
});
