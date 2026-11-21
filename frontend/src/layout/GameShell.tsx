import { useEffect, useRef, type ReactNode } from 'react';

interface GameShellProps {
  /** The league on screen; a new one moves focus to its heading. */
  gameKey: string;
  /** The tab on screen; a new one moves focus to the screen itself. */
  viewKey: string;
  children: ReactNode;
}

/** Focuses `selector`, or the page's <main> when it isn't on screen (a league that failed to load has no tab). */
function focusFirst(root: HTMLElement, selector: string) {
  (root.querySelector<HTMLElement>(selector) ?? root.querySelector<HTMLElement>('main'))?.focus();
}

/**
 * The full-screen Replay League game (#/play): no dashboard bar or menu, just the game. The page draws its own
 * <header> (the scorebug, with Exit, and the tabs) and its <main id="main-content">; the shell owns the skip link and
 * where focus goes on a change, the same contract as AppShell: [data-page-heading] for a new league,
 * [data-game-view] for a new tab.
 */
export function GameShell({ gameKey, viewKey, children }: GameShellProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const previous = useRef({ gameKey, viewKey });
  useEffect(() => {
    // Arriving from the dashboard (the lobby's Play link) unmounts whatever had focus.
    const root = rootRef.current;
    if (root && document.activeElement === document.body) focusFirst(root, '[data-page-heading]');
  }, []);
  useEffect(() => {
    const before = previous.current;
    previous.current = { gameKey, viewKey };
    const root = rootRef.current;
    if (!root) return;
    if (before.gameKey !== gameKey) focusFirst(root, '[data-page-heading]');
    else if (before.viewKey !== viewKey) focusFirst(root, '[data-game-view]');
  }, [gameKey, viewKey]);
  return (
    <div className="game" ref={rootRef}>
      <a className="skip-link" href="#main-content" onClick={(event) => {
        event.preventDefault();
        if (rootRef.current) focusFirst(rootRef.current, '[data-game-view]');
      }}>
        Skip to the game
      </a>
      {children}
    </div>
  );
}
