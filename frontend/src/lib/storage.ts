/**
 * localStorage access that never throws: private windows, blocked storage and quota
 * errors all degrade to "nothing stored" so the app keeps working with defaults.
 */

export function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

/** Writes (or removes, for `null`) a value. Returns false when storage is unavailable. */
export function writeStorage(key: string, value: string | null): boolean {
  try {
    if (value === null) {
      window.localStorage.removeItem(key);
    } else {
      window.localStorage.setItem(key, value);
    }
    return true;
  } catch {
    return false;
  }
}

export function readJsonStorage(key: string): unknown {
  const raw = readStorage(key);
  if (raw === null) return null;
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    return null;
  }
}

export function writeJsonStorage(key: string, value: unknown): boolean {
  return writeStorage(key, JSON.stringify(value));
}
