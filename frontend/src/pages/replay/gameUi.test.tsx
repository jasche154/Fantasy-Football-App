import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { DefenseChip, ScoreNumber, TeamBadge } from './gameUi';
import { teamRef } from './fixtures';

describe('gameUi', () => {
  it('shows a score with its label for screen readers', () => {
    render(<ScoreNumber value={102.4} label="Your score" />);
    expect(screen.getByText('Your score:', { exact: false }).parentElement).toHaveTextContent('Your score: 102.40');
  });

  it('marks the team and hides the tendency unless asked', () => {
    const { container } = render(<TeamBadge team={{ ...teamRef(2), tendency: 'Loves running backs' }} />);
    expect(container.querySelector('[data-team="2"]')).not.toBeNull();
    expect(screen.queryByText('Loves running backs')).toBeNull();
  });

  it('labels a defense chip in text and renders nothing without a rating', () => {
    render(<DefenseChip position="RB" rating={{ rank: 3, of: 32, tier: 'easy', factor: 1.12, allowed: 24.1 }} />);
    expect(screen.getByText('Easy · 3rd')).toBeInTheDocument();
    const { container } = render(<DefenseChip position="RB" rating={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
