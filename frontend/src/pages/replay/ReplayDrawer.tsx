import { useEffect, useId, useRef, type ReactNode } from 'react';
import { Icon } from '../../components/Icon';

interface ReplayDrawerProps {
  eyebrow: string;
  title: string;
  /** The dialog's accessible name when it should say more than the title. */
  label?: string;
  closeLabel?: string;
  /** 'wide': room for a player card's tables (a bottom sheet on phones). */
  size?: 'wide';
  onClose: () => void;
  children: ReactNode;
}

/** Makes every element outside `keep` (and its ancestors) inert, so Tab can't leave a modal; returns the undo. */
function inertOutside(keep: readonly Element[]): () => void {
  const changed: Element[] = [];
  const onPath = new Set<Element>(keep);
  for (let parent = keep[0]?.parentElement; parent && parent !== document.documentElement; parent = parent.parentElement) {
    for (const child of Array.from(parent.children)) {
      if (onPath.has(child) || child.hasAttribute('inert')) continue;
      child.setAttribute('inert', '');
      changed.push(child);
    }
    onPath.add(parent);
  }
  return () => changed.forEach((element) => element.removeAttribute('inert'));
}

/**
 * A right-hand dialog on the house .news-panel frame: focus moves in, the rest of the page is inert while it is open,
 * Escape closes, focus goes back to the opener.
 */
export function ReplayDrawer({ eyebrow, title, label, closeLabel = 'Close', size, onClose, children }: ReplayDrawerProps) {
  const titleId = useId();
  const closeRef = useRef<HTMLButtonElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const opener = document.activeElement;
    const keep = [panelRef.current, backdropRef.current].filter((element): element is HTMLElement => element !== null);
    const restore = inertOutside(keep);
    closeRef.current?.focus();
    return () => {
      restore();                      // first: an inert opener can't take focus back
      if (opener instanceof HTMLElement) opener.focus();
    };
  }, []);
  useEffect(() => {
    // Capture phase and preventDefault: Escape closes only the drawer, not also what is behind it (a lineup move).
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.preventDefault(); onClose(); } };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [onClose]);
  return (
    <>
      <div ref={backdropRef} className="news-panel__backdrop" onClick={onClose} aria-hidden="true" />
      <aside ref={panelRef} className={size === 'wide' ? 'news-panel replay-drawer--wide' : 'news-panel'} role="dialog" aria-modal="true" aria-label={label} aria-labelledby={label ? undefined : titleId}>
        <header className="news-panel__head">
          <div>
            <p className="news-panel__eyebrow">{eyebrow}</p>
            <h2 id={titleId} className="news-panel__title">{title}</h2>
          </div>
          <button ref={closeRef} type="button" className="btn btn--ghost btn--sm" aria-label={closeLabel} onClick={onClose}>
            <Icon name="close" size={16} />
          </button>
        </header>
        {children}
      </aside>
    </>
  );
}
