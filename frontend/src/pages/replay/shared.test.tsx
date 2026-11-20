import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { event, MOSTERT, NINERS, OPPONENT_TEAM_ID, playerRow, RESULT_EVENTS, summary, TUCKER, USER_TEAM_ID } from './fixtures';
import {
  ConfirmButton, EventList, formatPoints, formatRecord, groupEvents, ordinal, PositionTabs, positionParam, ReplayPlayerCell, TeamSelect,
} from './shared';

describe('formatting', () => {
  it('formats points, records and places', () => {
    expect([formatPoints(104.5), formatPoints(null)]).toEqual(['104.50', '—']);
    expect([formatRecord({ wins: 3, losses: 1, ties: 0 }), formatRecord({ wins: 3, losses: 1, ties: 1 })]).toEqual(['3-1', '3-1-1']);
    expect([1, 2, 3, 4, 11, 12, 13, 21, 22].map(ordinal)).toEqual(['1st', '2nd', '3rd', '4th', '11th', '12th', '13th', '21st', '22nd']);
  });
});

describe('ReplayPlayerCell', () => {
  it('opens the player from his name and shows team, opponent and badges', async () => {
    const onOpen = vi.fn();
    render(<ReplayPlayerCell player={MOSTERT} info={playerRow(MOSTERT, { out: true, trade_locked: true })} onOpen={onOpen} />);
    await userEvent.setup().click(screen.getByRole('button', { name: 'Raheem Mostert' }));
    expect(onOpen).toHaveBeenCalledWith(MOSTERT.id);
    expect(screen.getByText('RB')).toHaveClass('pos-chip--rb');
    expect(screen.getByText('vs DEN')).toBeInTheDocument();
    expect(screen.getByText('Out')).toBeInTheDocument();
    expect(screen.getByText('Trade-locked')).toBeInTheDocument();
  });

  it('shows a bye instead of an opponent, tones K and DST, and is plain text without onOpen', () => {
    render(<><ReplayPlayerCell player={TUCKER} info={{ opponent: 'BYE', bye: true }} /><ReplayPlayerCell player={NINERS} /></>);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.getByText('Bye')).toBeInTheDocument();
    expect(screen.queryByText(/vs/)).not.toBeInTheDocument();
    expect(screen.getByText('K')).toHaveClass('pos-chip--k');
    expect(screen.getByText('DST')).toHaveClass('pos-chip--dst');
  });
});

describe('PositionTabs', () => {
  it('offers FLEX only when asked and reports the pressed tab', async () => {
    const onChange = vi.fn();
    const { rerender } = render(<PositionTabs value="ALL" onChange={onChange} />);
    const labels = () => within(screen.getByRole('group', { name: 'Filter by position' })).getAllByRole('button').map((tab) => tab.textContent);
    expect(labels()).toEqual(['All', 'QB', 'RB', 'WR', 'TE', 'K', 'DST']);
    rerender(<PositionTabs value="K" onChange={onChange} withFlex />);
    expect(labels()).toEqual(['All', 'QB', 'RB', 'WR', 'TE', 'FLEX', 'K', 'DST']);
    expect(screen.getByRole('button', { name: 'K' })).toHaveAttribute('aria-pressed', 'true');
    await userEvent.setup().click(screen.getByRole('button', { name: 'FLEX' }));
    expect(onChange).toHaveBeenCalledWith('FLEX');
    expect([positionParam('ALL'), positionParam('FLEX')]).toEqual([undefined, 'FLEX']);
  });
});

describe('TeamSelect', () => {
  it('lists the user first, or leaves the user out', async () => {
    const onChange = vi.fn();
    const { rerender } = render(<TeamSelect game={summary()} value={USER_TEAM_ID} onChange={onChange} />);
    const options = () => within(screen.getByLabelText('Team')).getAllByRole('option').map((option) => option.textContent);
    expect(options().slice(0, 2)).toEqual(['Your Team (you)', 'Gridiron Ghosts']);
    expect(options()).toHaveLength(12);
    await userEvent.setup().selectOptions(screen.getByLabelText('Team'), String(OPPONENT_TEAM_ID));
    expect(onChange).toHaveBeenCalledWith(OPPONENT_TEAM_ID);
    rerender(<TeamSelect game={summary()} value={OPPONENT_TEAM_ID} onChange={onChange} excludeUser />);
    expect(options()).toHaveLength(11);
    expect(options()).not.toContain('Your Team (you)');
  });
});

describe('ConfirmButton', () => {
  it('needs two presses and can be cancelled', async () => {
    const onConfirm = vi.fn();
    const user = userEvent.setup();
    render(<ConfirmButton label="Drop" confirmLabel="Yes, drop" onConfirm={onConfirm} />);
    await user.click(screen.getByRole('button', { name: 'Drop' }));
    expect(screen.getByRole('button', { name: 'Yes, drop' })).toHaveFocus();
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onConfirm).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Drop' }));
    await user.click(screen.getByRole('button', { name: 'Yes, drop' }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'Drop' })).toBeInTheDocument();
  });

  it('names what it acts on in both states when given a subject', async () => {
    const user = userEvent.setup();
    render(<ConfirmButton label="Delete" confirmLabel="Yes, delete" subject="Sunday Replay" onConfirm={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'Delete Sunday Replay' }));
    expect(screen.getByRole('button', { name: 'Yes, delete Sunday Replay' })).toHaveTextContent('Yes, delete Sunday Replay');
  });
});

describe('events', () => {
  it('groups by kind with results first and lists lines, with the week on request', () => {
    const events = [event(1, 'draft', 'Pick 1', { week: 0 }), event(2, 'drop', 'A dropped B'), event(3, 'trade', 'A traded with B'),
      event(4, 'claim', 'A claimed C'), event(5, 'add', 'A added D'), event(6, 'champion', 'A won the championship'), ...RESULT_EVENTS];
    expect(groupEvents(events).map((group) => [group.title, group.events.map((item) => item.id)])).toEqual([
      ['Results', [6, 31, 32]], ['Waiver results', [4]], ['Trades', [3]], ['Adds and drops', [2, 5]], ['Draft picks', [1]],
    ]);
    render(<EventList events={[events[0], RESULT_EVENTS[0]]} showWeek />);
    const [pick, result] = screen.getAllByRole('listitem');
    expect(pick).toHaveTextContent('DraftPick 1');
    expect(result).toHaveTextContent('Wk 5Your Team beat Sunday Scaries 121.46-98.20');
    expect(result).toHaveClass('replay-events__item--mine');
  });
});
