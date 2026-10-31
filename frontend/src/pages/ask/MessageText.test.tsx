import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MessageText, toBlocks } from './MessageText';

describe('MessageText', () => {
  it('groups bullet and numbered lines into lists', () => {
    expect(toBlocks('Start Bijan.\n- Proj 18.2\n- Safe floor\n\n1. First\n2. Second\nDone')).toEqual([
      { kind: 'paragraph', text: 'Start Bijan.' },
      { kind: 'bullets', items: ['Proj 18.2', 'Safe floor'] },
      { kind: 'numbered', items: ['First', 'Second'] },
      { kind: 'paragraph', text: 'Done' },
    ]);
  });

  it('renders lists as list items and markup as plain text', () => {
    render(<MessageText text={'Picks:\n- <script>alert(1)</script>\n- Puka Nacua'} />);
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
    expect(screen.getByText('<script>alert(1)</script>')).toBeInTheDocument();
    expect(document.querySelector('script')).toBeNull();
  });
});
