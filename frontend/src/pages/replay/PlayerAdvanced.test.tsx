import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MAHOMES, playerDetail } from './fixtures';
import { advancedDetail, boxLine } from './fixturesPlayers';
import { PlayerAdvanced } from './PlayerAdvanced';

const cellsOf = (row: HTMLElement) => within(row).getAllByRole('cell').map((cell) => cell.textContent);
const tiles = () => Object.fromEntries(screen.getAllByRole('term').map((term) => [term.textContent, term.nextSibling?.textContent]));

describe('PlayerAdvanced', () => {
  it('sums up a receiver\'s season and shows the trend in words', () => {
    render(<PlayerAdvanced name="Nico Collins" position="WR" weeks={advancedDetail().weeks} />);
    expect(tiles()).toEqual({ Games: '5', 'Snap %': '79%', 'Target share': '21%', 'Targets / game': '6.4', 'Catch %': '69%', 'Yds / target': '8.1' });
    const trends = within(screen.getByRole('list', { name: 'Trends' })).getAllByRole('listitem');
    expect(trends[0]).toHaveTextContent('Snap %: Up, 85% last 3 vs 79% season');
    expect(trends[1]).toHaveTextContent('Target share: Down, 18% last 3 vs 21% season');
  });

  it('lists every week, with dashes for the bye and the week still to play', () => {
    render(<PlayerAdvanced name="Nico Collins" position="WR" weeks={advancedDetail().weeks} />);
    const table = within(screen.getByRole('table', { name: 'Nico Collins usage by week' }));
    expect(table.getAllByRole('columnheader').map((header) => header.textContent))
      .toEqual(['Wk', 'Opp', 'Snap', 'Tgt', 'Share', 'Rec', 'Ctch%', 'Y/Tgt', 'Y/Rec']);
    const rows = table.getAllByRole('row');
    expect(cellsOf(rows[1])).toEqual(['DEN', '70%', '8', '25%', '6', '75%', '10.0', '13.3']);
    expect(cellsOf(rows[3])).toEqual(['BYE', '—', '—', '—', '—', '—', '—', '—']);
    expect(cellsOf(rows[7])).toEqual(['DEN', '—', '—', '—', '—', '—', '—', '—']);
  });

  it('adds carries and touches for a back, and never divides by zero', () => {
    const weeks = advancedDetail().weeks.map((week) => (week.box ? { ...week, box: boxLine({ targets: 0, rec: 0, rec_yds: 0 }) } : week));
    render(<PlayerAdvanced name="Raheem Mostert" position="RB" weeks={weeks} />);
    const rows = within(screen.getByRole('table')).getAllByRole('row');
    expect(within(rows[0]).getAllByRole('columnheader').map((header) => header.textContent))
      .toEqual(['Wk', 'Opp', 'Snap', 'Car', 'Y/Car', 'Tch', 'Tgt', 'Share', 'Rec', 'Ctch%', 'Y/Tgt', 'Y/Rec']);
    expect(cellsOf(rows[1])).toEqual(['DEN', '70%', '15', '4.8', '15', '0', '25%', '0', '—', '—', '—']);
    expect(tiles()['Catch %']).toBe('—');
  });

  it('shows a quarterback\'s passing and rushing, and asks for more games before a trend', () => {
    const weeks = playerDetail(MAHOMES).weeks.map((week) => (week.usage
      ? { ...week, box: boxLine({ pass_cmp: 22, pass_att: 34, pass_yds: 280, pass_td: 2, pass_int: 1, carries: 4, rush_yds: 22 }) } : week));
    render(<PlayerAdvanced name="Patrick Mahomes" position="QB" weeks={weeks} />);
    const rows = within(screen.getByRole('table')).getAllByRole('row');
    expect(within(rows[0]).getAllByRole('columnheader').map((header) => header.textContent))
      .toEqual(['Wk', 'Opp', 'Snap', 'C/ATT', 'PaYd', 'TD:INT', 'Car', 'RuYd', 'Y/Car']);
    expect(cellsOf(rows[1])).toEqual(['DEN', '61%', '22/34', '280', '2:1', '4', '22', '5.5']);
    expect(tiles()['Cmp %']).toBe('65%');
    expect(screen.getByText('Trends show once he has played more than 3 games.')).toBeInTheDocument();
  });

  it('shows a dash for completions and attempts a saved league never recorded', () => {
    const weeks = playerDetail(MAHOMES).weeks.map((week) => (week.usage
      ? { ...week, box: boxLine({ pass_cmp: null, pass_att: null, pass_yds: 280, pass_td: 2, pass_int: 1 }) } : week));
    render(<PlayerAdvanced name="Patrick Mahomes" position="QB" weeks={weeks} />);
    const rows = within(screen.getByRole('table')).getAllByRole('row');
    expect(cellsOf(rows[1]).slice(0, 3)).toEqual(['DEN', '61%', '—']);
    expect(tiles()['Cmp %']).toBe('—');
  });
});
