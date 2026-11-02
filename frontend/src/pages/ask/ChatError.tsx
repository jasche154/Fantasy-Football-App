import { useState } from 'react';
import { ApiError } from '../../api/client';
import { ErrorState } from '../../components/StatusViews';
import { LLM_SETUP_MESSAGE, TOO_LARGE_MESSAGE } from './toolCalls';

function status(error: Error): number | null {
  return error instanceof ApiError ? error.status : null;
}

interface ChatErrorProps {
  error: Error;
  /** Seconds before the free-tier limit allows another question (from useChat). */
  waitSeconds?: number;
  onRetry?: () => void;
}

/**
 * The alert holds only fixed text, so it's announced once. The ticking seconds sit outside any
 * live region, and a polite status says once when asking again is possible.
 */
/** A wait this long is most likely the provider's daily cap, not its per-minute one (the server already
 * waits out per-minute limits of up to 45 s itself). */
export const DAILY_CAP_SECONDS = 120;

/** "45 s", "12 min", "3 h 5 min". */
export function formatWait(seconds: number): string {
  if (seconds < DAILY_CAP_SECONDS) return `${seconds} s`;
  const minutes = Math.ceil(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const rest = minutes % 60;
  return rest ? `${Math.floor(minutes / 60)} h ${rest} min` : `${minutes / 60} h`;
}

function RateLimited({ waitSeconds = 0, onRetry }: Omit<ChatErrorProps, 'error'>) {
  const isWaiting = waitSeconds > 0;
  // Fixed when the error appears, so the alert text doesn't change as the countdown ticks.
  const [isDaily] = useState(waitSeconds >= DAILY_CAP_SECONDS);
  const [resetsIn] = useState(() => formatWait(waitSeconds));
  return (
    <div className="state state--error">
      <div className="state__body">
        <div role="alert">
          <p className="state__title">{isDaily ? 'Daily free-tier limit reached' : 'Free-tier limit reached'}</p>
          <p className="state__text">
            {isDaily
              ? `The AI provider’s free allowance is used up; it resets in about ${resetsIn}. Add a second provider to LLM_CHAIN in backend/.env (for example gemini) to keep asking.`
              : 'The free AI tier allows a limited amount of text per minute, and the last question used it up.'}
          </p>
        </div>
        {isWaiting && <p className="state__text">You can try again in {formatWait(waitSeconds)}.</p>}
        <p className="state__text" role="status">{isWaiting ? '' : 'You can ask again now.'}</p>
        {onRetry && (
          <button type="button" className="btn btn--ghost btn--sm" aria-disabled={isWaiting}
            onClick={() => { if (!isWaiting) onRetry(); }}>
            {isWaiting ? 'Try again (waiting)' : 'Try again'}
          </button>
        )}
      </div>
    </div>
  );
}

/** One error view per failure kind: not configured (503), too much data (413), rate limit (429), the rest. */
export function ChatError({ error, waitSeconds, onRetry }: ChatErrorProps) {
  const code = status(error);
  if (code === 429) return <RateLimited waitSeconds={waitSeconds} onRetry={onRetry} />;
  if (code === 503) return <ErrorState error={error} title="AI assistant not configured" message={LLM_SETUP_MESSAGE} />;
  if (code === 413) {
    return <ErrorState error={error} title="That question is too big" message={TOO_LARGE_MESSAGE} onRetry={onRetry} />;
  }
  return <ErrorState error={error} title="The assistant couldn’t answer" onRetry={onRetry} />;
}
