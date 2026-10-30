/** Which players' news you've opened, kept in this browser, so new notes can stand out (like Yahoo's news icon). */
const STORAGE_KEY = 'gridiron.newsSeen';
/** News newer than this, and not opened yet, counts as new. */
export const NEW_NEWS_HOURS = 48;

export type SeenNews = Readonly<Record<string, string>>;   // player id -> newest published time you've opened

export function loadSeen(): SeenNews {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}');
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? (parsed as SeenNews) : {};
  } catch {
    return {};
  }
}

export function markSeen(seen: SeenNews, playerId: number, published: string): SeenNews {
  const next = { ...seen, [String(playerId)]: published };
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // storage blocked: the badge clears for this visit only
  }
  return next;
}

/** 'new' (recent and not opened), 'seen' (has news), or null (no news). */
export function newsState(latest: string | undefined, seen: SeenNews, playerId: number, now: number = Date.now()): 'new' | 'seen' | null {
  if (!latest) return null;
  const published = Date.parse(latest);
  if (Number.isNaN(published)) return 'seen';
  const opened = seen[String(playerId)];
  const isRecent = now - published <= NEW_NEWS_HOURS * 3600 * 1000;
  return isRecent && (!opened || Date.parse(opened) < published) ? 'new' : 'seen';
}
