import { useEffect, useRef } from 'react';
import type { ToolCallTrace } from '../../api/types';
import { Icon } from '../../components/Icon';
import type { ChatEntry } from './useChat';
import { summarizeArguments } from './toolCalls';

function ToolCallChips({ calls }: { calls: readonly ToolCallTrace[] }) {
  if (calls.length === 0) return null;
  return (
    <div className="tool-calls">
      <p className="tool-calls__label">Data used</p>
      <ul className="tool-calls__list">
        {calls.map((call, index) => {
          const args = summarizeArguments(call.arguments ?? {});
          return (
            <li key={`${call.name}-${index}`} className={call.ok ? 'tool-chip' : 'tool-chip tool-chip--failed'}>
              <Icon name={call.ok ? 'check' : 'alert'} size={14} />
              <code className="tool-chip__name">{call.name}</code>
              {args && <span className="tool-chip__args">{args}</span>}
              <span className={call.ok ? 'sr-only' : 'tool-chip__status'}>{call.ok ? 'succeeded' : 'failed'}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function MessageBubble({ entry }: { entry: ChatEntry }) {
  const isUser = entry.role === 'user';
  return (
    <li className={isUser ? 'message message--user' : 'message message--assistant'}>
      <p className="message__author">{isUser ? 'You' : 'Assistant'}</p>
      <div className="message__bubble">
        <p className="message__text">{entry.content}</p>
      </div>
      {!isUser && <ToolCallChips calls={entry.toolCalls} />}
    </li>
  );
}

interface ChatMessagesProps {
  entries: readonly ChatEntry[];
  isPending: boolean;
}

export function ChatMessages({ entries, isPending }: ChatMessagesProps) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: 'end', behavior: 'smooth' });
  }, [entries.length, isPending]);
  return (
    <div className="chat-log" role="log" aria-live="polite" aria-label="Conversation">
      <ol className="chat-log__list">
        {entries.map((entry) => (
          <MessageBubble key={entry.id} entry={entry} />
        ))}
      </ol>
      {isPending && (
        <p className="message message--assistant message--pending" role="status">
          <span className="typing" aria-hidden="true"><span /><span /><span /></span>
          Looking up the data…
        </p>
      )}
      <div ref={endRef} />
    </div>
  );
}
