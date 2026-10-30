import type { NewsItem } from '../api/types';

function when(iso: string | null): string {
  if (!iso) return '';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

/** Headlines and short summaries from Sleeper's news partners, newest first, each credited and linked. */
export function NewsList({ items, showPlayer = false }: { items: readonly NewsItem[]; showPlayer?: boolean }) {
  return (
    <ul className="news-list">
      {items.map((item) => (
        <li key={`${item.player?.id ?? ''}-${item.published}-${item.title}`} className="news-item">
          <p className="news-item__title">
            {showPlayer && item.player && <span className="news-item__player">{item.player.name}: </span>}
            {item.title}
          </p>
          {item.summary && <p className="news-item__summary">{item.summary}</p>}
          <p className="news-item__meta">
            {when(item.published)} · {item.source}
            {item.url && <> · <a href={item.url} target="_blank" rel="noopener noreferrer">Full note</a></>}
          </p>
        </li>
      ))}
    </ul>
  );
}
