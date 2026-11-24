import { useEffect, useSyncExternalStore } from 'react';
import type { ReplaySummary } from '../../api/types';

/** How many league-chat lines the user has seen per game, for the unread dot. localStorage is the truth
 * whenever it can be read (shared by every tab, so a delete in one tab is seen in the others); this page's
 * memory is only the fallback when storage throws (then the dot shows again after a reload).
 * League ids can come back after a delete (SQLite reuses the highest one), so the count is stored as
 * "<created_at>:<count>": a count saved for another league with the same id (or an old plain number, whose
 * league is unknown) counts as nothing seen. Deleting a league also forgets its count. */
const EVENT = 'replay-chat-seen';
const memory = new Map<number, string>();     // the stored value, "<created_at>:<count>"
const reading = new Set<number>();
const READING = -1;
const key = (gameId: number): string => `replay-chat-seen:${gameId}`;

/** A league by its id and creation stamp: the stamp tells a reused id apart. */
export type ChatLeague = Pick<ReplaySummary, 'id' | 'created_at'>;

function changed(): void {
  window.dispatchEvent(new Event(EVENT));
}

/** The count in a stored value when it was saved for this league; 0 for another league or an old plain number. */
function countFor(league: ChatLeague, stored: string | null | undefined): number {
  const at = stored?.lastIndexOf(':') ?? -1;
  if (!stored || at < 0 || stored.slice(0, at) !== String(league.created_at)) return 0;
  const count = Number(stored.slice(at + 1));
  return Number.isFinite(count) && count > 0 ? count : 0;
}

const unsaved = new Set<number>();            // games whose last write failed (quota): memory is newer than storage

export function readChatSeen(league: ChatLeague): number {
  if (unsaved.has(league.id)) return countFor(league, memory.get(league.id));
  try {
    return countFor(league, window.localStorage.getItem(key(league.id)));
  } catch {
    return countFor(league, memory.get(league.id));    // storage blocked: memory only
  }
}

/** The user has seen `count` lines (the chat's total as just loaded). */
export function markChatSeen(league: ChatLeague, count: number): void {
  const value = `${league.created_at}:${count}`;
  // After a failed write memory already holds it: try storage again, or a reload would bring the dot back.
  if (!unsaved.has(league.id) && readChatSeen(league) === count && memory.get(league.id) === value) return;
  memory.set(league.id, value);
  try {
    window.localStorage.setItem(key(league.id), value);
    unsaved.delete(league.id);
  } catch {
    unsaved.add(league.id);                       // readable but not writable: this tab trusts its memory
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

/** True when the league chat (`game.chat` lines so far) has lines the user has not opened yet. */
export function useChatUnread(game: ChatLeague & Pick<ReplaySummary, 'chat'>): boolean {
  const seen = useSyncExternalStore(subscribe, () => (reading.has(game.id) ? READING : readChatSeen(game)), () => 0);
  if (seen === READING) return false;
  return game.chat > (seen > game.chat ? 0 : seen);
}
