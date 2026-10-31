import { useCallback, useEffect, useRef, useState } from 'react';
import { sendChat } from '../../api/actions';
import { isAbortError } from '../../api/client';
import type { ChatMessage, ChatResponse } from '../../api/types';
import { loadChat, saveChat, type ChatEntry } from './chatStorage';

export type { ChatEntry } from './chatStorage';

/** "failed" withdraws the message so the caller can restore it; "aborted" means New chat. */
export type SendOutcome = 'ok' | 'failed' | 'aborted' | 'skipped';

/** Only the latest messages go to the server; remembered facts carry what matters from earlier. */
export const MAX_HISTORY = 20;
/** The server accepts assistant messages up to this length (user messages up to 4,000). */
export const MAX_ASSISTANT_CHARS = 12000;

export interface ChatController {
  entries: readonly ChatEntry[];
  memory: readonly string[];
  leagueId: number | null;
  isPending: boolean;
  error: Error | null;
  /** The question that just failed, for Retry. */
  lastFailed: string | null;
  send: (text: string) => Promise<SendOutcome>;
  setLeagueId: (id: number | null) => void;
  reset: () => void;
  dismissError: () => void;
}

/** Long replies are cut before going back as history, so one long answer can't break the chat. */
export function toMessage(entry: ChatEntry): ChatMessage {
  const isLong = entry.role === 'assistant' && entry.content.length > MAX_ASSISTANT_CHARS;
  return { role: entry.role, content: isLong ? `${entry.content.slice(0, MAX_ASSISTANT_CHARS - 1)}…` : entry.content };
}

function toError(caught: unknown): Error {
  return caught instanceof Error ? caught : new Error(String(caught));
}

function replyFields(response: ChatResponse): Omit<ChatEntry, 'id' | 'role' | 'content'> {
  return {
    toolCalls: response.tool_calls ?? [],
    suggestions: response.suggestions ?? [],
    unverified: response.unverified ?? [],
    dataAsOf: response.data_as_of,
    stopReason: response.stop_reason,
    provider: response.provider ?? null,
  };
}

export function useChat(): ChatController {
  const [initial] = useState(loadChat);
  const [entries, setEntries] = useState<readonly ChatEntry[]>(initial.entries);
  const [memory, setMemory] = useState<readonly string[]>(initial.memory);
  const [leagueId, setLeagueId] = useState<number | null>(initial.leagueId);
  const [isPending, setIsPending] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [lastFailed, setLastFailed] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const nextId = useRef(0);

  useEffect(() => () => controllerRef.current?.abort(), []);
  useEffect(() => saveChat({ entries, memory, leagueId }), [entries, memory, leagueId]);

  const makeEntry = (role: ChatEntry['role'], content: string, extra?: Partial<ChatEntry>): ChatEntry => {
    nextId.current += 1;
    return { id: `msg-${nextId.current}`, role, content, toolCalls: [], ...extra };
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
    setLastFailed(null);
    try {
      const messages = history.slice(-MAX_HISTORY).map(toMessage);
      const request = leagueId === null ? { messages, memory } : { messages, memory, league_id: leagueId };
      const response = await sendChat(request, controller.signal);
      if (controller.signal.aborted) return 'aborted';
      if (response.memory) setMemory(response.memory);
      setEntries((current) => [...current, makeEntry('assistant', response.reply, replyFields(response))]);
      return 'ok';
    } catch (caught) {
      if (isAbortError(caught)) return 'aborted';
      setEntries((current) => current.filter((entry) => entry.id !== userEntry.id));
      setError(toError(caught));
      setLastFailed(content);
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
    setLastFailed(null);
    setIsPending(false);
  }, []);

  const dismissError = useCallback(() => setError(null), []);
  return { entries, memory, leagueId, isPending, error, lastFailed, send, setLeagueId, reset, dismissError };
}
