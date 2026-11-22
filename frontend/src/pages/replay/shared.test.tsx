import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { event, MOSTERT, NINERS, OPPONENT_TEAM_ID, playerRow, RESULT_EVENTS, summary, tagged, TUCKER, USER_TEAM_ID } from './fixtures';
import {
  canGoOnIr, ConfirmButton, EventList, formatPoints, formatRecord, groupEvents, injuryLabel, isHardTag, ordinal, PositionTabs, positionParam,
  ReplayDrawer, ReplayInjuryBadge, ReplayIrBlockedNotice, ReplayPlayerCell, TeamSelect, viewHash,
} from './shared';

describe('formatting', () => {
  it('formats points, records and places', () => {
    expect([formatPoints(104.5), formatPoints(null)]).toEqual(['104.50', '—']);
    expect([formatRecord({ wins: 3, losses: 1, ties: 0 }), formatRecord({ wins: 3, losses: 1, ties: 1 })]).toEqual(['3-1', '3-1-1']);
    expect([1, 2, 3, 4, 11, 12, 13, 21, 22].map(ordinal)).toEqual(['1st', '2nd', '3rd', '4th', '11th', '12th', '13th', '21st', '22nd']);
  });
});

describe('injury tags', () => {
  it('labels, tones and classifies every tag', () => {
    const cases = (['Q', 'D', 'O', 'IR', 'PUP', 'SUS'] as const).map((tag) => {
      const injury = tagged(MOSTERT, tag).injury!;
      return [tag, injuryLabel(injury), isHardTag(injury), canGoOnIr(injury)];
    });
    expect(cases).toEqual([
      ['Q', 'Questionable', false, false], ['D', 'Doubtful', false, false], ['O', 'Out', true, true],
      ['IR', 'Injured reserve', true, true], ['PUP', 'Physically unable to perform', true, true], ['SUS', 'Suspended', true, false],
    ]);
    expect([isHardTag(null), canGoOnIr(undefined)]).toEqual([false, false]);
    expect(injuryLabel(tagged(MOSTERT, 'Q', 'LP', 'Hamstring').injury!)).toBe('Questionable: Hamstring (limited practice)');
    expect(injuryLabel(tagged(MOSTERT, 'IR', null, 'Groin').injury!)).toBe('Injured reserve: Groin');
    expect(injuryLabel(tagged(MOSTERT, 'O', 'DNP').injury!)).toBe('Out (did not practice)');
    expect(injuryLabel(tagged(MOSTERT, 'D', 'FP').injury!)).toBe('Doubtful (full practice)');
  });

  it('shows the badge with its full label for screen readers and a tone per tag', () => {
    render(<><ReplayInjuryBadge injury={tagged(MOSTERT, 'Q', 'LP', 'Hamstring').injury!} /><ReplayInjuryBadge injury={tagged(MOSTERT, 'SUS').injury!} /></>);
    const q = screen.getByTitle('Questionable: Hamstring (limited practice)');
    expect(q).toHaveClass('status-badge', 'status-badge--missing');
    expect(within(q).getByText('Q')).toHaveAttribute('aria-hidden', 'true');
    expect(within(q).getByText('Questionable: Hamstring (limited practice)')).toHaveClass('sr-only');
    expect(screen.getByTitle('Suspended')).toHaveClass('status-badge--out');
  });

  it('shows Out only when no hard tag already says so', () => {
    const { unmount } = render(<ReplayPlayerCell player={tagged(MOSTERT, 'O')} info={{ out: true }} />);
    expect(screen.queryByText('Out', { selector: '.status-badge' })).not.toBeInTheDocument();
    expect(screen.getByText('O')).toBeInTheDocument();
    unmount();
    render(<ReplayPlayerCell player={tagged(MOSTERT, 'Q')} info={{ out: true }} />);
    expect(screen.getByText('Q')).toBeInTheDocument();
    expect(screen.getByText('Out', { selector: '.status-badge' })).toBeInTheDocument();
  });

  it('shows the IR banner only while blocked and roster moves are open', () => {
    const { container, rerender } = render(<ReplayIrBlockedNotice game={summary()} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<ReplayIrBlockedNotice game={summary({ ir_blocked: [MOSTERT], phase: 'complete' })} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<ReplayIrBlockedNotice game={summary({ ir_blocked: [MOSTERT, TUCKER] })} />);
    expect(screen.getByRole('status')).toHaveTextContent('Raheem Mostert and Justin Tucker is on IR but no longer has an O, IR or PUP tag. '
      + 'Activate or drop him before you add, claim or trade for players.');
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
    expect(groupEvents([event(7, 'ir', 'A moved B to IR')]).map((group) => group.title)).toEqual(['Adds and drops']);
    render(<EventList events={[events[0], RESULT_EVENTS[0]]} showWeek />);
    const [pick, result] = screen.getAllByRole('listitem');
    expect(pick).toHaveTextContent('DraftPick 1');
    expect(result).toHaveTextContent('Wk 5Your Team beat Sunday Scaries 121.46-98.20');
    expect(result).toHaveClass('replay-events__item--mine');
  });
});

describe('the game screen', () => {
  it('builds each tab\'s #/play URL', () => {
    expect(viewHash(7, 'trades')).toBe('#/play?game=7&view=trades');
  });

  it('makes the rest of the page inert while the drawer is open, and gives focus back on close', () => {
    const { unmount } = render(
      <>
        <button type="button">Open card</button>
        <main><p>Behind</p></main>
      </>,
    );
    const opener = screen.getByRole('button', { name: 'Open card' });
    opener.focus();
    const drawer = render(<ReplayDrawer eyebrow="Player" title="Raheem Mostert" onClose={vi.fn()}>card</ReplayDrawer>);
    expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus();
    expect(opener.parentElement).toHaveAttribute('inert');
    expect(screen.getByRole('dialog').parentElement).not.toHaveAttribute('inert');
    drawer.unmount();
    expect(opener.parentElement).not.toHaveAttribute('inert');
    expect(opener).toHaveFocus();
    unmount();
  });

  it('opens a wide drawer only when asked', () => {
    const { unmount } = render(<ReplayDrawer eyebrow="Player" title="Raheem Mostert" size="wide" onClose={vi.fn()}>card</ReplayDrawer>);
    expect(screen.getByRole('dialog', { name: 'Raheem Mostert' })).toHaveClass('news-panel', 'replay-drawer--wide');
    unmount();
    render(<ReplayDrawer eyebrow="Player" title="Raheem Mostert" onClose={vi.fn()}>card</ReplayDrawer>);
    expect(screen.getByRole('dialog', { name: 'Raheem Mostert' })).not.toHaveClass('replay-drawer--wide');
  });
});
