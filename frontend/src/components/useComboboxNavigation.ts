import { useEffect, useState, type KeyboardEvent } from 'react';

export function moveIndex(current: number, delta: 1 | -1, count: number): number {
  if (count === 0) return -1;
  if (current < 0) return delta === 1 ? 0 : count - 1;
  return (current + delta + count) % count;
}

interface ComboboxNavigation<T> {
  isOpen: boolean;
  isExpanded: boolean;
  /** Index of the highlighted option, or -1. Always within the current options. */
  active: number;
  open: () => void;
  /** Re-open with no highlight (e.g. after the query text changes). */
  reopen: () => void;
  close: () => void;
  setActive: (index: number) => void;
  choose: (option: T) => void;
  handleKeyDown: (event: KeyboardEvent<HTMLInputElement>) => void;
}

/** Keeps the highlighted option visible inside a scrolling listbox. */
function useScrollIntoView(elementId: string | null): void {
  useEffect(() => {
    if (elementId) document.getElementById(elementId)?.scrollIntoView?.({ block: 'nearest' });
  }, [elementId]);
}

/**
 * Popup + highlight state for an ARIA combobox: ArrowUp/ArrowDown move (wrapping),
 * Enter picks the highlighted option (or the first one when none is highlighted),
 * Escape closes the popup and then clears the input.
 * The highlight tracks the option itself, so when the option list is replaced (new results,
 * excluded players) it drops instead of silently pointing at a different player.
 */
export function useComboboxNavigation<T>(
  options: readonly T[],
  onChoose: (option: T) => void,
  onClear: () => void,
  optionId: (index: number) => string,
): ComboboxNavigation<T> {
  const [isOpen, setIsOpen] = useState(false);
  const [highlighted, setHighlighted] = useState<T | null>(null);
  const active = highlighted === null ? -1 : options.indexOf(highlighted);
  const isExpanded = isOpen && options.length > 0;
  useScrollIntoView(active >= 0 ? optionId(active) : null);

  const setActive = (index: number) => setHighlighted(index >= 0 ? (options[index] ?? null) : null);

  const choose = (option: T) => {
    onChoose(option);
    setHighlighted(null);
    setIsOpen(false);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      setIsOpen(true);
      setActive(moveIndex(active, event.key === 'ArrowDown' ? 1 : -1, options.length));
    } else if (event.key === 'Enter' && isExpanded) {
      // With nothing highlighted, Enter takes the best (first) match.
      event.preventDefault();
      choose(options[active >= 0 ? active : 0]);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      if (isExpanded) setIsOpen(false);
      else onClear();
      setHighlighted(null);
    }
  };

  return {
    isOpen,
    isExpanded,
    active,
    open: () => setIsOpen(true),
    reopen: () => {
      setHighlighted(null);
      setIsOpen(true);
    },
    close: () => setIsOpen(false),
    setActive,
    choose,
    handleKeyDown,
  };
}
