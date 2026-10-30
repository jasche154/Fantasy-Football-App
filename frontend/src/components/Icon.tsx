const ICON_PATHS = {
  home: 'M3 11.5 12 4l9 7.5M5.5 10v10h5v-6h3v6h5V10',
  team: 'M12 3 4 6v6c0 4.4 3.4 8.2 8 9 4.6-.8 8-4.6 8-9V6l-8-3Z',
  rankings: 'M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01',
  calendar: 'M4 6h16v14H4zM4 10h16M8 3v4M16 3v4',
  chart: 'M4 20h16M7 16v-5M12 16V6M17 16v-8',
  stats: 'M4 20V11M10 20V4M16 20v-6M21 20H3',
  trade: 'M7 4 3 8l4 4M3 8h13M17 20l4-4-4-4M21 16H8',
  market: 'm3 17 6-6 4 4 8-8M15 7h6v6',
  ask: 'M20 12a8 8 0 0 1-11.6 7.1L4 20l1-4.3A8 8 0 1 1 20 12ZM8.5 12h.01M12 12h.01M15.5 12h.01',
  settings:
    'M4 7h9M17 7h3M15 5v4M4 17h3M11 17h9M9 15v4M4 12h11M19 12h1M17 10v4',
  sun: 'M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6 7 7M17 17l1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z',
  moon: 'M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z',
  more: 'M5 12h.01M12 12h.01M19 12h.01',
  close: 'M6 6l12 12M18 6 6 18',
  swap: 'M7 3v14M3 13l4 4 4-4M17 21V7M13 11l4-4 4 4',
  send: 'M4 12 20 4l-5 16-3-7-8-1Z',
  external: 'M14 4h6v6M20 4l-9 9M18 14v6H4V6h6',
  search: 'M11 18a7 7 0 1 1 0-14 7 7 0 0 1 0 14ZM20 20l-4-4',
  plus: 'M12 5v14M5 12h14',
  check: 'm5 12 5 5 9-10',
  alert: 'M12 8v5M12 16.5h.01M10.3 4.2 2.6 18a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 4.2a2 2 0 0 0-3.4 0Z',
} as const;

export type IconName = keyof typeof ICON_PATHS;

interface IconProps {
  name: IconName;
  size?: number;
  className?: string;
}

/** Decorative stroke icon; always pair it with visible or screen-reader text. */
export function Icon({ name, size = 20, className }: IconProps) {
  return (
    <svg
      className={className ? `icon ${className}` : 'icon'}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={name === 'more' ? 3 : 1.9}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d={ICON_PATHS[name]} />
    </svg>
  );
}

/** Brand mark: a football with laces. */
export function BrandMark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <rect width="32" height="32" rx="9" className="brand-mark__bg" />
      <path
        d="M8 24c0-9 7-16 16-16 0 9-7 16-16 16Z"
        className="brand-mark__ball"
      />
      <path
        d="m12.5 19.5 7-7M14 15l1.5 1.5M16 13l1.5 1.5M12 17l1.5 1.5"
        className="brand-mark__laces"
      />
    </svg>
  );
}
