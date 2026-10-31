import { useEffect, useRef, useState } from 'react';
import type { ChatEntry } from './chatStorage';
import { MessageFooter } from './MessageFooter';
import { MessageText } from './MessageText';

/** After this long, the pending row also shows how long the assistant has been working. */
const SHOW_ELAPSED_AFTER_S = 5;

function MessageBubble({ entry }: { entry: ChatEntry }) {
  const isUser = entry.role === 'user';
  return (
    <li className={isUser ? 'message message--user' : 'message message--assistant'}>
      <p className="message__author">{isUser ? 'You' : 'Assistant'}</p>
      <div className="message__bubble">
        {isUser ? <p className="message__text">{entry.content}</p> : <MessageText text={entry.content} />}
      </div>
      {!isUser && <MessageFooter entry={entry} />}
    </li>
  );
}

function useElapsedSeconds(isRunning: boolean): number {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    setSeconds(0);
    if (!isRunning) return undefined;
    const timer = window.setInterval(() => setSeconds((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [isRunning]);
  return seconds;
}

function lastAssistant(entries: readonly ChatEntry[]): ChatEntry | undefined {
  return [...entries].reverse().find((entry) => entry.role === 'assistant');
}

interface ChatMessagesProps {
  entries: readonly ChatEntry[];
  isPending: boolean;
}

/**
 * The log itself is silent (aria-live="off"); one hidden status announces each new reply, so
 * nothing is read twice. Replies restored from storage aren't announced.
 */
export function ChatMessages({ entries, isPending }: ChatMessagesProps) {
  const endRef = useRef<HTMLDivElement>(null);
  const [restoredId] = useState(() => lastAssistant(entries)?.id);
  const elapsed = useElapsedSeconds(isPending);
  const latest = lastAssistant(entries);
  const announcement = latest && latest.id !== restoredId ? `Assistant replied: ${latest.content}` : '';

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: 'end', behavior: 'smooth' });
  }, [entries.length, isPending]);

  return (
    <>
      <div className="chat-log" role="log" aria-live="off" aria-label="Conversation">
        <ol className="chat-log__list">
          {entries.map((entry) => (
            <MessageBubble key={entry.id} entry={entry} />
          ))}
        </ol>
      </div>
      {isPending && (
        <div className="message message--assistant message--pending">
          <span className="typing" aria-hidden="true"><span /><span /><span /></span>
          <span role="status">Looking up the data…</span>
          {elapsed >= SHOW_ELAPSED_AFTER_S && <span className="message__elapsed">{elapsed} s · answers can take up to 30 s</span>}
        </div>
      )}
      <p className="sr-only" role="status" aria-live="polite">{announcement}</p>
      <div ref={endRef} />
    </>
  );
}
