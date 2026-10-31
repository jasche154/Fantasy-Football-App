import { useEffect, useState } from 'react';
import type { ToolCallTrace } from '../../api/types';
import { Icon } from '../../components/Icon';
import type { ChatEntry } from './chatStorage';
import { summarizeArguments, toolLabel } from './toolCalls';

const COPIED_MS = 2000;

function sourcesSummary(calls: readonly ToolCallTrace[], week: number | null | undefined): string {
  const count = `${calls.length} ${calls.length === 1 ? 'source' : 'sources'}`;
  return week ? `${count} · week ${week} data` : count;
}

function Sources({ calls, week }: { calls: readonly ToolCallTrace[]; week: number | null | undefined }) {
  if (calls.length === 0) return null;
  return (
    <details className="sources">
      <summary className="sources__summary">Sources: {sourcesSummary(calls, week)}</summary>
      <ul className="tool-calls__list">
        {calls.map((call, index) => {
          const args = summarizeArguments(call.arguments ?? {});
          return (
            <li key={`${call.name}-${index}`} className={call.ok ? 'tool-chip' : 'tool-chip tool-chip--failed'}>
              <Icon name={call.ok ? 'check' : 'alert'} size={14} />
              <span className="tool-chip__name" title={call.name}>{toolLabel(call.name)}</span>
              {args && <span className="tool-chip__args">{args}</span>}
              <span className={call.ok ? 'sr-only' : 'tool-chip__status'}>{call.ok ? 'succeeded' : 'failed'}</span>
            </li>
          );
        })}
      </ul>
    </details>
  );
}

function CopyButton({ text }: { text: string }) {
  const [status, setStatus] = useState<'idle' | 'copied' | 'failed'>('idle');
  useEffect(() => {
    if (status === 'idle') return undefined;
    const timer = window.setTimeout(() => setStatus('idle'), COPIED_MS);
    return () => window.clearTimeout(timer);
  }, [status]);
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setStatus('copied');
    } catch {
      setStatus('failed');
    }
  };
  const label = { idle: 'Copy', copied: 'Copied', failed: 'Copy failed' }[status];
  return (
    <>
      <button type="button" className="btn btn--ghost btn--sm message__copy" onClick={() => void handleCopy()}>
        {label}
      </button>
      <span className="sr-only" role="status">{status === 'idle' ? '' : label}</span>
    </>
  );
}

/** Under each assistant reply: notes about trust, the data used, and Copy. */
export function MessageFooter({ entry }: { entry: ChatEntry }) {
  const unverified = entry.unverified ?? [];
  return (
    <div className="message__footer">
      {unverified.length > 0 && (
        <p className="message__note message__note--warn">
          <Icon name="alert" size={14} /> Not matched to the app’s data: {unverified.join(', ')}. Double-check before acting.
        </p>
      )}
      {entry.stopReason === 'out_of_rounds' && (
        <p className="message__note">Partial answer: the assistant ran out of lookups. Ask a narrower follow-up for the rest.</p>
      )}
      <div className="message__actions">
        <Sources calls={entry.toolCalls} week={entry.dataAsOf?.week} />
        <CopyButton text={entry.content} />
      </div>
    </div>
  );
}
