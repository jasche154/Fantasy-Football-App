import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MessageText, toBlocks } from './MessageText';

describe('MessageText', () => {
  it('groups bullet and numbered lines into lists', () => {
    expect(toBlocks('Start Bijan.\n- Proj 18.2\n- Safe floor\n\n1. First\n2. Second\nDone')).toEqual([
      { kind: 'paragraph', text: 'Start Bijan.' },
      { kind: 'bullets', items: ['Proj 18.2', 'Safe floor'] },
      { kind: 'numbered', start: 1, items: ['First', 'Second'] },
      { kind: 'paragraph', text: 'Done' },
    ]);
  });

  it('keeps one list across blank lines and continues numbering', () => {
    expect(toBlocks('1. A\n\n2. B')).toEqual([{ kind: 'numbered', start: 1, items: ['A', 'B'] }]);
    render(<MessageText text={'More:\n3. C\n4. D'} />);
    expect(screen.getByRole('list')).toHaveAttribute('start', '3');
  });

  it('renders lists as list items and markup as plain text', () => {
    render(<MessageText text={'Picks:\n- <script>alert(1)</script>\n- Puka Nacua'} />);
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
    expect(screen.getByText('<script>alert(1)</script>')).toBeInTheDocument();
    expect(document.querySelector('script')).toBeNull();
  });
});
