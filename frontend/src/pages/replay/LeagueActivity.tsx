import { useState, type ReactNode } from 'react';
import { getReplayActivity } from '../../api/replay';
import type { ReplayEvent } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { EmptyState, Skeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import type { ReplayViewProps } from './shared';

const PAGE_SIZE = 25;

/** `kind` is the query param: one event kind or several joined by commas. */
const FILTERS: readonly { label: string; kind?: string }[] = [
  { label: 'All' },
  { label: 'Trades', kind: 'trade' },
  { label: 'Waiver claims', kind: 'claim' },
  { label: 'Adds/Drops', kind: 'add,drop,ir' },
  { label: 'Results', kind: 'result,playoffs,champion' },
];

/** The event's sentence with each player's name turned into a button that opens his card. */
function eventText(event: ReplayEvent, onOpen: (playerId: number) => void): ReactNode {
  const byName = new Map(event.players.map((player) => [player.name, player]));
  if (byName.size === 0) return event.text;
  const longestFirst = [...byName.keys()].sort((a, b) => b.length - a.length);
  const pattern = new RegExp(`(${longestFirst.map((name) => name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`);
  return event.text.split(pattern).map((part, index) => {
    const player = byName.get(part);
    // The split keeps the order, so the index is a stable key.
    return player
      ? <button key={index} type="button" className="replay-player__name" onClick={() => onOpen(player.id)}>{part}</button>
      : part;
  });
}

/** The league's log, newest first, by kind and a page at a time. */
export function LeagueActivity({ game, version, openPlayer }: ReplayViewProps) {
  const [filter, setFilter] = useState(FILTERS[0]);
  const [offset, setOffset] = useState(0);
  const log = useAsync(
    (signal) => getReplayActivity(game.id, { kind: filter.kind, limit: PAGE_SIZE, offset }, signal),
    [game.id, filter, offset, version],
  );
  return (
    <>
      <div className="segmented replay-segmented" role="group" aria-label="Filter activity">
        {FILTERS.map((option) => (
          <button key={option.label} type="button" aria-pressed={option === filter}
            className={`segmented__option${option === filter ? ' is-active' : ''}`}
            onClick={() => { setFilter(option); setOffset(0); }}>
            {option.label}
          </button>
        ))}
      </div>
      <AsyncView state={log} loading={<Skeleton lines={8} label="Loading activity…" />} errorTitle="Couldn't load the activity"
        isEmpty={(data) => data.total === 0} empty={<EmptyState title="Nothing here yet" />}>
        {(data) => (
          <>
            <ul className="replay-events" aria-label="League activity">
              {data.results.map((event) => (
                <li key={event.id} className={event.team?.is_user ? 'replay-events__item replay-events__item--mine' : 'replay-events__item'}>
                  <span className="replay-events__week">{event.week > 0 ? `Wk ${event.week}` : 'Draft'}</span>
                  {eventText(event, openPlayer)}
                </li>
              ))}
            </ul>
            <div className="replay-pager">
              <button type="button" className="btn btn--ghost btn--sm" disabled={data.offset === 0}
                onClick={() => setOffset(Math.max(0, data.offset - PAGE_SIZE))}>Newer</button>
              <span className="hint" role="status">
                {data.results.length > 0 ? `${data.offset + 1}–${data.offset + data.results.length} of ${data.total}` : `0 of ${data.total}`}
              </span>
              <button type="button" className="btn btn--ghost btn--sm" disabled={data.offset + data.results.length >= data.total}
                onClick={() => setOffset(data.offset + PAGE_SIZE)}>Older</button>
            </div>
          </>
        )}
      </AsyncView>
    </>
  );
}
