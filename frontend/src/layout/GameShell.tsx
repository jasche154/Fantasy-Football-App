import { useEffect, useRef, type ReactNode } from 'react';

interface GameShellProps {
  /** The league on screen; a new one moves focus to its heading. */
  gameKey: string;
  /** The tab on screen; a new one moves focus to the screen itself. */
  viewKey: string;
  children: ReactNode;
}

function focusFirst(root: HTMLElement, selector: string) {
  (root.querySelector<HTMLElement>(selector) ?? root).focus();
}

/**
 * The full-screen Replay League game (#/play): no dashboard bar or menu, just the game. The page draws its own
 * scorebug (with Exit); the shell owns the skip link, the <main> landmark and where focus goes on a change,
 * the same contract as AppShell: [data-page-heading] for a new league, [data-game-view] for a new tab.
 */
export function GameShell({ gameKey, viewKey, children }: GameShellProps) {
  const mainRef = useRef<HTMLElement>(null);
  const previous = useRef({ gameKey, viewKey });
  useEffect(() => {
    // Arriving from the dashboard (the lobby's Play link) unmounts whatever had focus.
    const main = mainRef.current;
    if (main && document.activeElement === document.body) focusFirst(main, '[data-page-heading]');
  }, []);
  useEffect(() => {
    const before = previous.current;
    previous.current = { gameKey, viewKey };
    const main = mainRef.current;
    if (!main) return;
    if (before.gameKey !== gameKey) focusFirst(main, '[data-page-heading]');
    else if (before.viewKey !== viewKey) focusFirst(main, '[data-game-view]');
  }, [gameKey, viewKey]);
  return (
    <div className="game">
      <a className="skip-link" href="#main-content" onClick={(event) => {
        event.preventDefault();
        if (mainRef.current) focusFirst(mainRef.current, '[data-game-view]');
      }}>
        Skip to the game
      </a>
      <main id="main-content" className="game-main" ref={mainRef} tabIndex={-1}>
        {children}
      </main>
    </div>
  );
}
