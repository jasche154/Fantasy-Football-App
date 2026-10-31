import type { NewsItem } from '../api/types';

function when(iso: string | null): string {
  if (!iso) return '';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

/** Headlines and short summaries from Sleeper's news partners, newest first, each credited and linked. */
interface NewsListProps {
  items: readonly NewsItem[];
  showPlayer?: boolean;
  showAnalysis?: boolean;
  /** Marks an item as new (highlighted with a "New" tag). */
  isNew?: (item: NewsItem) => boolean;
  /** Makes the player's name a button (e.g. to open his news panel). */
  onPlayer?: (player: NonNullable<NewsItem['player']>) => void;
}

export function NewsList({ items, showPlayer = false, showAnalysis = false, isNew, onPlayer }: NewsListProps) {
  return (
    <ul className="news-list">
      {items.map((item) => {
        const fresh = isNew?.(item) ?? false;
        const player = item.player;
        return (
        <li key={`${player?.id ?? ''}-${item.published}-${item.title}`} className={`news-item${fresh ? ' news-item--new' : ''}`}>
          <p className="news-item__title">
            {fresh && <><span className="news-item__tag">New</span>{' '}</>}
            {showPlayer && player && (onPlayer
              ? <><button type="button" className="news-item__player linklike" onClick={() => onPlayer(player)}>{player.name}</button>: </>
              : <span className="news-item__player">{player.name}: </span>)}
            {item.title}
          </p>
          {item.summary && <p className="news-item__summary">{item.summary}</p>}
          {showAnalysis && item.analysis && <p className="news-item__analysis">{item.analysis}</p>}
          <p className="news-item__meta">
            {when(item.published)} · {item.source}
            {item.url && <> · <a href={item.url} target="_blank" rel="noopener noreferrer">Full note</a></>}
          </p>
        </li>
        );
      })}
    </ul>
  );
}
