import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../api/client';
import { ChatError, DEFAULT_RETRY_SECONDS } from './ChatError';

describe('ChatError', () => {
  afterEach(() => vi.useRealTimers());

  it('counts down a rate limit before Retry works', () => {
    vi.useFakeTimers();
    const onRetry = vi.fn();
    render(<ChatError error={new ApiError('limit', 429, 'limit', 3)} onRetry={onRetry} />);

    expect(screen.getByRole('alert')).toHaveTextContent('Free-tier limit reached');
    fireEvent.click(screen.getByRole('button', { name: 'Try again in 3 s' }));
    expect(onRetry).not.toHaveBeenCalled();

    act(() => { vi.advanceTimersByTime(3000); });
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it('waits a default minute when the server sends no Retry-After', () => {
    render(<ChatError error={new ApiError('limit', 429)} onRetry={() => undefined} />);
    expect(screen.getByRole('button', { name: `Try again in ${DEFAULT_RETRY_SECONDS} s` })).toBeInTheDocument();
  });

  it('explains a too-large question', () => {
    render(<ChatError error={new ApiError('too big', 413)} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Ask about fewer players or one league');
  });
});
