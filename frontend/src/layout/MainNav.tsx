import { Fragment, useCallback, useEffect, useId, useRef, useState, type RefObject } from 'react';
import { Icon } from '../components/Icon';
import { buildHash } from '../router/routes';
import type { Route } from '../router/routes';
import { useMeta } from '../state/MetaContext';
import { NAV_ITEMS, navLabel, type NavItem } from './navItems';

interface NavLinkProps {
  item: NavItem;
  isActive: boolean;
  className: string;
  onNavigate?: () => void;
}

function NavLink({ item, isActive, className, onNavigate }: NavLinkProps) {
  const meta = useMeta();
  return (
    <a
      href={buildHash(item.path)}
      className={isActive ? `${className} is-active` : className}
      aria-current={isActive ? 'page' : undefined}
      onClick={onNavigate}
    >
      <Icon name={item.icon} />
      <span className="nav-link__label">{navLabel(item, meta.data?.current_week)}</span>
      <span className="nav-link__short">{item.shortLabel}</span>
    </a>
  );
}

/** Closes a popup on outside pointer-down or Escape, returning focus to its toggle. */
function useDismiss(
  isOpen: boolean,
  close: () => void,
  wrapperRef: RefObject<HTMLElement | null>,
  toggleRef: RefObject<HTMLElement | null>,
): void {
  useEffect(() => {
    if (!isOpen) return undefined;
    const handlePointer = (event: PointerEvent) => {
      if (!wrapperRef.current?.contains(event.target as Node)) close();
    };
    const handleKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      // Return focus to the toggle so it isn't stranded inside a hidden menu.
      if (wrapperRef.current?.contains(document.activeElement)) toggleRef.current?.focus();
      close();
    };
    document.addEventListener('pointerdown', handlePointer);
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('pointerdown', handlePointer);
      document.removeEventListener('keydown', handleKey);
    };
  }, [isOpen, close, wrapperRef, toggleRef]);
}

function MoreMenu({ active }: { active: Route['name'] }) {
  const [isOpen, setIsOpen] = useState(false);
  const menuId = useId();
  const wrapperRef = useRef<HTMLLIElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const secondary = NAV_ITEMS.filter((item) => !item.isPrimary);
  const hasActive = secondary.some((item) => item.route === active);
  const close = useCallback(() => setIsOpen(false), []);
  useDismiss(isOpen, close, wrapperRef, toggleRef);

  return (
    <li
      className="nav-more"
      ref={wrapperRef}
      onBlur={(event) => {
        // Only close when focus moved to another element (e.g. Tab). A null target means a
        // click on something non-focusable, which the pointerdown listener already handles.
        const next = event.relatedTarget;
        if (next && !event.currentTarget.contains(next)) close();
      }}
    >
      <button
        ref={toggleRef}
        type="button"
        className={hasActive ? 'nav-link nav-link--tab is-active' : 'nav-link nav-link--tab'}
        aria-expanded={isOpen}
        aria-controls={menuId}
        onClick={() => setIsOpen((value) => !value)}
      >
        <Icon name="more" />
        <span className="nav-link__short">More</span>
      </button>
      <ul id={menuId} className="nav-more__menu" hidden={!isOpen}>
        {secondary.map((item) => (
          <li key={item.route}>
            <NavLink item={item} isActive={item.route === active} className="nav-link nav-link--menu" onNavigate={close} />
          </li>
        ))}
      </ul>
    </li>
  );
}

/** One nav landmark: a sidebar on wide screens, a bottom tab bar (+ "More") on phones. */
export function MainNav({ active }: { active: Route['name'] }) {
  return (
    <nav className="main-nav" aria-label="Main">
      <ul className="main-nav__list">
        {NAV_ITEMS.map((item, index) => (
          <Fragment key={item.route}>
            {item.group === 'research' && NAV_ITEMS[index - 1]?.group !== 'research' && (
              <li className="main-nav__heading" aria-hidden="true">Research</li>
            )}
            <li className={item.isPrimary ? 'main-nav__item' : 'main-nav__item main-nav__item--secondary'}>
              <NavLink item={item} isActive={item.route === active} className="nav-link nav-link--tab" />
            </li>
          </Fragment>
        ))}
        <MoreMenu active={active} />
      </ul>
    </nav>
  );
}
