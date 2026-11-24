import { useEffect, useSyncExternalStore } from 'react';

/** How many league-chat lines the user has seen per game, for the unread dot. localStorage is the truth
 * whenever it can be read (shared by every tab, so a delete in one tab is seen in the others); this page's
 * memory is only the fallback when storage throws (then the dot shows again after a reload).
 * League ids can come back after a delete (SQLite reuses the highest one), so deleting a league forgets its
 * count, and a count above the league's chat (left by a deleted league) counts as nothing seen. */
const EVENT = 'replay-chat-seen';
const memory = new Map<number, number>();
const reading = new Set<number>();
const READING = -1;
const key = (gameId: number): string => `replay-chat-seen:${gameId}`;

function changed(): void {
  window.dispatchEvent(new Event(EVENT));
}

const unsaved = new Set<number>();            // games whose last write failed (quota): memory is newer than storage

export function readChatSeen(gameId: number): number {
  if (unsaved.has(gameId)) return memory.get(gameId) ?? 0;
  try {
    const raw = Number(window.localStorage.getItem(key(gameId)));
    return Number.isFinite(raw) && raw > 0 ? raw : 0;
  } catch {
    return memory.get(gameId) ?? 0;              // storage blocked: memory only
  }
}

/** The user has seen `count` lines (the chat's total as just loaded). */
export function markChatSeen(gameId: number, count: number): void {
  if (readChatSeen(gameId) === count && memory.get(gameId) === count) return;
  memory.set(gameId, count);
  try {
    window.localStorage.setItem(key(gameId), String(count));
    unsaved.delete(gameId);
  } catch {
    unsaved.add(gameId);                          // readable but not writable: this tab trusts its memory
  }
  changed();
}

/** The league is gone: its id may be reused by the next one. */
export function forgetChatSeen(gameId: number): void {
  memory.delete(gameId);
  unsaved.delete(gameId);
  try {
    window.localStorage.removeItem(key(gameId));
  } catch {
    // storage blocked: memory only
  }
  changed();
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener(EVENT, onChange);
  window.addEventListener('storage', onChange);
  return () => {
    window.removeEventListener(EVENT, onChange);
    window.removeEventListener('storage', onChange);
  };
}

/** While the chat is on screen no dot shows anywhere: its newest lines are loading and get marked seen. */
export function useChatReading(gameId: number): void {
  useEffect(() => {
    reading.add(gameId);
    changed();
    return () => {
      reading.delete(gameId);
      changed();
    };
  }, [gameId]);
}

/** True when the league chat has lines the user has not opened yet. */
export function useChatUnread(gameId: number, total: number): boolean {
  const seen = useSyncExternalStore(subscribe, () => (reading.has(gameId) ? READING : readChatSeen(gameId)), () => 0);
  if (seen === READING) return false;
  return total > (seen > total ? 0 : seen);
}
