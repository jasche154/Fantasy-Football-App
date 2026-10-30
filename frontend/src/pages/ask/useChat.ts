import { useCallback, useEffect, useRef, useState } from 'react';
import { sendChat } from '../../api/actions';
import { isAbortError } from '../../api/client';
import type { ChatMessage, ToolCallTrace } from '../../api/types';

export interface ChatEntry {
  id: string;
  role: ChatMessage['role'];
  content: string;
  toolCalls: readonly ToolCallTrace[];
}

/** "failed" withdraws the message so the caller can restore it; "aborted" means New chat. */
export type SendOutcome = 'ok' | 'failed' | 'aborted' | 'skipped';

/** Only the latest messages go to the server; remembered facts carry what matters from earlier. */
export const MAX_HISTORY = 20;

export interface ChatController {
  entries: readonly ChatEntry[];
  memory: readonly string[];
  isPending: boolean;
  error: Error | null;
  send: (text: string) => Promise<SendOutcome>;
  reset: () => void;
  dismissError: () => void;
}

function toMessage(entry: ChatEntry): ChatMessage {
  return { role: entry.role, content: entry.content };
}

function toError(caught: unknown): Error {
  return caught instanceof Error ? caught : new Error(String(caught));
}

export function useChat(): ChatController {
  const [entries, setEntries] = useState<readonly ChatEntry[]>([]);
  const [memory, setMemory] = useState<readonly string[]>([]);
  const [isPending, setIsPending] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const nextId = useRef(0);

  useEffect(() => () => controllerRef.current?.abort(), []);

  const makeEntry = (role: ChatEntry['role'], content: string, toolCalls: readonly ToolCallTrace[] = []): ChatEntry => {
    nextId.current += 1;
    return { id: `msg-${nextId.current}`, role, content, toolCalls };
  };

  const send = async (text: string): Promise<SendOutcome> => {
    const content = text.trim();
    if (!content || isPending) return 'skipped';
    const userEntry = makeEntry('user', content);
    const history = [...entries, userEntry];
    const controller = new AbortController();
    controllerRef.current = controller;
    setEntries(history);
    setIsPending(true);
    setError(null);
    try {
      const response = await sendChat(history.slice(-MAX_HISTORY).map(toMessage), memory, controller.signal);
      if (controller.signal.aborted) return 'aborted';
      if (response.memory) setMemory(response.memory);
      setEntries((current) => [...current, makeEntry('assistant', response.reply, response.tool_calls ?? [])]);
      return 'ok';
    } catch (caught) {
      if (isAbortError(caught)) return 'aborted';
      setEntries((current) => current.filter((entry) => entry.id !== userEntry.id));
      setError(toError(caught));
      return 'failed';
    } finally {
      if (!controller.signal.aborted) setIsPending(false);
    }
  };

  const reset = useCallback(() => {
    controllerRef.current?.abort();
    setEntries([]);
    setMemory([]);
    setError(null);
    setIsPending(false);
  }, []);

  const dismissError = useCallback(() => setError(null), []);
  return { entries, memory, isPending, error, send, reset, dismissError };
}
