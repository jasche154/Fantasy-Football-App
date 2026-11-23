import { useSyncExternalStore } from 'react';

/** How many league-chat lines the user has seen per game, for the unread dot. localStorage when it works,
 * else this page's memory (a private window or blocked storage just shows the dot again after a reload). */
const EVENT = 'replay-chat-seen';
const memory = new Map<number, number>();
const key = (gameId: number): string => `replay-chat-seen:${gameId}`;

export function readChatSeen(gameId: number): number {
  let stored = 0;
  try {
    const raw = Number(window.localStorage.getItem(key(gameId)));
    if (Number.isFinite(raw) && raw > 0) stored = raw;
  } catch {
    // storage blocked: memory only
  }
  return Math.max(stored, memory.get(gameId) ?? 0);
}

export function markChatSeen(gameId: number, count: number): void {
  if (readChatSeen(gameId) >= count) return;
  memory.set(gameId, count);
  try {
    window.localStorage.setItem(key(gameId), String(count));
  } catch {
    // storage blocked: memory only
  }
  window.dispatchEvent(new Event(EVENT));
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener(EVENT, onChange);
  window.addEventListener('storage', onChange);
  return () => {
    window.removeEventListener(EVENT, onChange);
    window.removeEventListener('storage', onChange);
  };
}

/** True when the league chat has lines the user has not opened yet. */
export function useChatUnread(gameId: number, total: number): boolean {
  const seen = useSyncExternalStore(subscribe, () => readChatSeen(gameId), () => 0);
  return total > seen;
}
