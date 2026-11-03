import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { ChatEntry } from './chatStorage';
import { MessageFooter } from './MessageFooter';

function entry(overrides: Partial<ChatEntry> = {}): ChatEntry {
  return {
    id: 'msg-1',
    role: 'assistant',
    content: 'Your roster in Family League (My Squad), week 9 projections:',
    toolCalls: [{ name: 'get_my_team', arguments: { league: 'Family League' }, ok: true }],
    ...overrides,
  };
}

const INSTANT = /answered instantly from the app.s data, without ai/i;

describe('MessageFooter', () => {
  it('says when the app answered without AI', () => {
    render(<MessageFooter entry={entry({ answeredBy: 'app', provider: null })} />);
    expect(screen.getByText(INSTANT)).toBeInTheDocument();
    expect(screen.getByText(/Sources: 1 source/)).toBeInTheDocument();
  });

  it('shows no note for AI answers or older saved replies', () => {
    const { rerender } = render(<MessageFooter entry={entry({ answeredBy: 'ai', provider: 'groq' })} />);
    expect(screen.queryByText(INSTANT)).not.toBeInTheDocument();
    rerender(<MessageFooter entry={entry()} />);
    expect(screen.queryByText(INSTANT)).not.toBeInTheDocument();
  });
});
