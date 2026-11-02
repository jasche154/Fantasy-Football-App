import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../api/client';
import { ChatError, formatWait } from './ChatError';

const RATE_LIMIT = new ApiError('limit', 429, 'limit', 3);

describe('ChatError', () => {
  it('keeps the ticking seconds out of the alert and blocks Retry while waiting', () => {
    const onRetry = vi.fn();
    render(<ChatError error={RATE_LIMIT} waitSeconds={3} onRetry={onRetry} />);

    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('Free-tier limit reached');
    expect(alert).not.toHaveTextContent('3 s');
    expect(screen.getByText('You can try again in 3 s.')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('');

    fireEvent.click(screen.getByRole('button', { name: 'Try again (waiting)' }));
    expect(onRetry).not.toHaveBeenCalled();
  });

  it('says once, politely, when asking again is possible', () => {
    const onRetry = vi.fn();
    render(<ChatError error={RATE_LIMIT} waitSeconds={0} onRetry={onRetry} />);

    expect(screen.getByRole('status')).toHaveTextContent('You can ask again now.');
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it('explains a too-large question and still offers Retry for an edited one', () => {
    const onRetry = vi.fn();
    render(<ChatError error={new ApiError('too big', 413)} onRetry={onRetry} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Ask about fewer players or one league');
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it('shows setup help without Retry when the assistant is not configured', () => {
    render(<ChatError error={new ApiError('no key', 503)} onRetry={() => undefined} />);
    expect(screen.getByRole('alert')).toHaveTextContent('AI assistant not configured');
    expect(screen.getByRole('alert')).toHaveTextContent('Add GROQ_API_KEY to backend/.env');
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument();
  });
});

describe('ChatError daily cap', () => {
  it('names a long wait as the daily allowance and shows it in hours and minutes', () => {
    render(<ChatError error={new ApiError('limit', 429, 'limit', 7500)} waitSeconds={7500} onRetry={() => undefined} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Daily free-tier limit reached');
    expect(screen.getByRole('alert')).toHaveTextContent('LLM_CHAIN');
    expect(screen.getByRole('alert')).toHaveTextContent('resets in about 2 h 5 min');
    expect(screen.getByText('You can try again in 2 h 5 min.')).toBeInTheDocument();
  });

  it('keeps the daily label while the countdown runs out', () => {
    const error = new ApiError('limit', 429, 'limit', 7500);
    const view = render(<ChatError error={error} waitSeconds={7500} onRetry={() => undefined} />);
    view.rerender(<ChatError error={error} waitSeconds={0} onRetry={() => undefined} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Daily free-tier limit reached');
    expect(screen.getByRole('status')).toHaveTextContent('You can ask again now.');
  });

  it('keeps the per-minute wording for a short wait', () => {
    render(<ChatError error={new ApiError('limit', 429, 'limit', 60)} waitSeconds={60} onRetry={() => undefined} />);
    expect(screen.getByRole('alert')).toHaveTextContent(/^Free-tier limit reached/);
  });

  it('formats waits', () => {
    expect(formatWait(45)).toBe('45 s');
    expect(formatWait(119)).toBe('119 s');
    expect(formatWait(120)).toBe('2 min');
    expect(formatWait(600)).toBe('10 min');
    expect(formatWait(3600)).toBe('1 h');
    expect(formatWait(3660)).toBe('1 h 1 min');
  });
});
