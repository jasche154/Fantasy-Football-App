import { useEffect, useState } from 'react';

function secondsLeft(deadline: number | null): number {
  return deadline === null ? 0 : Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
}

/**
 * Whole seconds until `deadline` (epoch ms); 0 when there's none or it passed. Computed on each
 * render (so it's right on the very first one) and re-rendered once a second while counting.
 */
export function useSecondsUntil(deadline: number | null): number {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (deadline === null || secondsLeft(deadline) === 0) return undefined;
    const timer = window.setInterval(() => {
      setTick((tick) => tick + 1);
      if (secondsLeft(deadline) === 0) window.clearInterval(timer);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [deadline]);
  return secondsLeft(deadline);
}
