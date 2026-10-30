import { useEffect, useRef } from 'react';
import { getPlayerNews } from '../api/data';
import type { PlayerSummary } from '../api/types';
import { useAsync } from '../hooks/useAsync';
import { AsyncView } from './AsyncView';
import { Icon } from './Icon';
import { NewsList } from './NewsList';
import { EmptyState, Skeleton } from './StatusViews';

interface NewsButtonProps {
  player: PlayerSummary;
  state: 'new' | 'seen' | null;
  onOpen: (player: PlayerSummary) => void;
}

/** The news icon next to a player: highlighted while there's news you haven't opened. */
export function NewsButton({ player, state, onOpen }: NewsButtonProps) {
  if (state === null) return null;
  const label = state === 'new' ? `New news for ${player.name}` : `News for ${player.name}`;
  return (
    <button type="button" className={`news-btn news-btn--${state}`} aria-label={label} title={label}
      onClick={() => onOpen(player)}>
      <Icon name="news" size={15} />
      {state === 'new' && <span className="news-btn__dot" aria-hidden="true" />}
    </button>
  );
}

/** Side panel with one player's latest notes; Escape or the close button dismisses it. */
export function NewsPanel({ player, onClose }: { player: PlayerSummary; onClose: () => void }) {
  const news = useAsync((signal) => getPlayerNews(player.id, signal), [player.id]);
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <>
      <div className="news-panel__backdrop" onClick={onClose} aria-hidden="true" />
      <aside className="news-panel" role="dialog" aria-modal="true" aria-label={`${player.name} news`}>
        <header className="news-panel__head">
          <div>
            <p className="news-panel__eyebrow">Player news</p>
            <h2 className="news-panel__title">{player.name}</h2>
            {(player.position || player.team) && <p className="news-panel__sub">{[player.position, player.team].filter(Boolean).join(' · ')}</p>}
          </div>
          <button ref={closeRef} type="button" className="btn btn--ghost btn--sm" aria-label="Close news" onClick={onClose}>
            <Icon name="close" size={16} />
          </button>
        </header>
        <AsyncView state={news} loading={<Skeleton lines={5} label="Loading news…" />}
          isEmpty={(d) => d.items.length === 0} empty={<EmptyState title="No recent news." />}
          errorTitle="News is unavailable right now">
          {(d) => <NewsList items={d.items} showAnalysis />}
        </AsyncView>
        <p className="hint attribution">News via Sleeper from FantasyPros and RotoWire.</p>
      </aside>
    </>
  );
}
