import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { DefenseChip, ScoreNumber, TeamBadge } from './gameUi';
import { teamRef } from './fixtures';

function setReducedMotion(reduce: boolean) {
  vi.stubGlobal('matchMedia', (query: string) => ({ matches: reduce && query.includes('reduce'), media: query,
    addEventListener: () => {}, removeEventListener: () => {} }));
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function digits(container: HTMLElement): string {
  return container.querySelector('.game-score > [aria-hidden="true"]')?.textContent ?? '';
}

describe('ScoreNumber', () => {
  it('shows a score with its label for screen readers', () => {
    render(<ScoreNumber value={102.4} label="Your score" />);
    expect(screen.getByText('Your score: 102.40')).toHaveClass('sr-only');
  });

  it('shows the final value at once under reduced motion, even when asked to count up', () => {
    setReducedMotion(true);
    const { container } = render(<ScoreNumber value={121.46} label="Your score" countUp />);
    expect(digits(container)).toBe('121.46');
  });

  it('counts up from 0 to the value, while screen readers get the final value from the start', async () => {
    setReducedMotion(false);
    let now = 0;
    const frames: FrameRequestCallback[] = [];
    vi.spyOn(performance, 'now').mockImplementation(() => now);
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => frames.push(callback));
    vi.stubGlobal('cancelAnimationFrame', () => {});
    const { container } = render(<ScoreNumber value={100} label="Your score" countUp />);
    expect(digits(container)).toBe('0.00');
    expect(screen.getByText('Your score: 100.00')).toBeInTheDocument();
    const step = (ms: number) => act(() => { now = ms; frames.splice(0).forEach((frame) => frame(ms)); });
    step(300);
    expect(Number(digits(container))).toBeGreaterThan(50);
    expect(Number(digits(container))).toBeLessThan(100);
    step(600);
    expect(digits(container)).toBe('100.00');
  });

  it('does not animate without countUp', () => {
    setReducedMotion(false);
    const { container } = render(<ScoreNumber value={98.2} size="sm" label="Opponent" />);
    expect(digits(container)).toBe('98.20');
    expect(container.querySelector('.game-score--sm')).not.toBeNull();
  });
});

describe('TeamBadge', () => {
  it('marks the team and hides the tendency unless asked', () => {
    const { container } = render(<TeamBadge team={{ ...teamRef(2), tendency: 'Loves running backs' }} />);
    expect(container.querySelector('[data-team="2"]')).not.toBeNull();
    expect(screen.queryByText('Loves running backs')).toBeNull();
  });

  it('shows a CPU\'s tendency when asked, never the user\'s, and names the user\'s team', () => {
    render(<TeamBadge team={{ ...teamRef(2), tendency: 'Loves running backs' }} showTendency />);
    expect(screen.getByText('Loves running backs')).toBeInTheDocument();
    const { container } = render(<TeamBadge team={{ ...teamRef(4), tendency: 'Hidden' }} showTendency />);
    expect(screen.queryByText('Hidden')).toBeNull();
    expect(container.querySelector('.game-team--you')).toHaveTextContent('Your Team (you)');
  });

  it('shows nothing extra for a CPU with no tendency yet', () => {
    const { container } = render(<TeamBadge team={teamRef(2)} showTendency />);
    expect(container.querySelector('.game-team__tendency')).toBeNull();
  });
});

describe('DefenseChip', () => {
  it('labels a defense chip in text, with the full sentence for screen readers, and renders nothing without a rating', () => {
    render(<DefenseChip position="RB" rating={{ rank: 3, of: 32, tier: 'easy', factor: 1.12, allowed: 24.1 }} />);
    expect(screen.getByText('Easy · 3rd')).toHaveClass('game-chip', 'game-chip--easy');
    expect(screen.getByText(/ranks 3rd of 32 in points allowed to RBs \(24.1 a game\)/)).toHaveClass('sr-only');
    const { container } = render(<DefenseChip position="RB" rating={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('says "Tough" in text for the bottom quarter', () => {
    render(<DefenseChip position="WR" rating={{ rank: 29, of: 32, tier: 'tough', factor: 0.84, allowed: 18 }} />);
    expect(screen.getByText('Tough · 29th')).toHaveClass('game-chip--tough');
  });
});
