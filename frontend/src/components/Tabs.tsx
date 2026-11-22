import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';

export interface TabItem {
  id: string;
  label: string;
  panel: ReactNode;
}

interface TabsProps {
  /** Names the tab list for screen readers. */
  label: string;
  tabs: readonly TabItem[];
  className?: string;
}

/** WAI-ARIA tabs: the first tab starts open; arrow keys, Home and End move between tabs (automatic activation). */
export function Tabs({ label, tabs, className }: TabsProps) {
  const base = useId();
  const [active, setActive] = useState(0);
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const current = Math.min(active, tabs.length - 1);

  function select(index: number) {
    setActive(index);
    buttons.current[index]?.focus();
  }

  function onKeyDown(event: KeyboardEvent) {
    const last = tabs.length - 1;
    const moves: Record<string, number> = {
      ArrowRight: current === last ? 0 : current + 1,
      ArrowLeft: current === 0 ? last : current - 1,
      Home: 0,
      End: last,
    };
    if (event.altKey || event.ctrlKey || event.metaKey || !(event.key in moves)) return;
    event.preventDefault();
    select(moves[event.key]);
  }

  return (
    <div className={className}>
      <div role="tablist" aria-label={label} className="segmented" onKeyDown={onKeyDown}>
        {tabs.map((tab, index) => (
          <button key={tab.id} type="button" role="tab" id={`${base}-tab-${tab.id}`} aria-controls={`${base}-panel-${tab.id}`}
            aria-selected={index === current} tabIndex={index === current ? 0 : -1}
            ref={(element) => { buttons.current[index] = element; }} onClick={() => setActive(index)}
            className={`segmented__option${index === current ? ' is-active' : ''}`}>
            {tab.label}
          </button>
        ))}
      </div>
      {tabs.map((tab, index) => (
        <div key={tab.id} role="tabpanel" id={`${base}-panel-${tab.id}`} aria-labelledby={`${base}-tab-${tab.id}`}
          hidden={index !== current} tabIndex={0}>
          {tab.panel}
        </div>
      ))}
    </div>
  );
}
