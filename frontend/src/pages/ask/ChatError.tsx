import { useEffect, useState } from 'react';
import { ApiError } from '../../api/client';
import { ErrorState } from '../../components/StatusViews';
import { LLM_SETUP_MESSAGE, TOO_LARGE_MESSAGE } from './toolCalls';

/** Used when a 429 comes without a Retry-After header. */
export const DEFAULT_RETRY_SECONDS = 60;

function status(error: Error): number | null {
  return error instanceof ApiError ? error.status : null;
}

function useCountdown(seconds: number): number {
  const [left, setLeft] = useState(seconds);
  useEffect(() => {
    setLeft(seconds);
    if (seconds <= 0) return undefined;
    const timer = window.setInterval(() => setLeft((value) => Math.max(0, value - 1)), 1000);
    return () => window.clearInterval(timer);
  }, [seconds]);
  return left;
}

interface ChatErrorProps {
  error: Error;
  onRetry?: () => void;
}

function RateLimited({ error, onRetry }: ChatErrorProps) {
  const wait = error instanceof ApiError && error.retryAfter ? error.retryAfter : DEFAULT_RETRY_SECONDS;
  const left = useCountdown(wait);
  return (
    <div className="state state--error" role="alert">
      <div className="state__body">
        <p className="state__title">Free-tier limit reached</p>
        <p className="state__text">
          The free AI tier allows a limited amount of text per minute, and the last question used it up.
          {left > 0 ? ` You can try again in ${left} s.` : ' You can try again now.'}
        </p>
        {onRetry && (
          <button type="button" className="btn btn--ghost btn--sm" aria-disabled={left > 0}
            onClick={() => { if (left === 0) onRetry(); }}>
            {left > 0 ? `Try again in ${left} s` : 'Try again'}
          </button>
        )}
      </div>
    </div>
  );
}

/** One error view per failure kind: not configured (503), too much data (413), rate limit (429), the rest. */
export function ChatError({ error, onRetry }: ChatErrorProps) {
  const code = status(error);
  if (code === 429) return <RateLimited error={error} onRetry={onRetry} />;
  if (code === 503) return <ErrorState error={error} title="AI assistant not configured" message={LLM_SETUP_MESSAGE} />;
  if (code === 413) {
    return <ErrorState error={error} title="That question is too big" message={TOO_LARGE_MESSAGE} onRetry={onRetry} />;
  }
  return <ErrorState error={error} title="The assistant couldn’t answer" onRetry={onRetry} />;
}
