import { useState } from 'react';
import type { ReplayEvent } from '../../api/types';
import { EventList, groupEvents } from './shared';

const SHOWN_PER_GROUP = 6;

interface EventsPanelProps {
  events: readonly ReplayEvent[];
  onDismiss: () => void;
}

/** "What just happened": the last action's events by kind (results first), long groups cut until asked. */
export function EventsPanel({ events, onDismiss }: EventsPanelProps) {
  const [showAll, setShowAll] = useState(false);
  const groups = groupEvents(events);
  const isLong = groups.some((group) => group.events.length > SHOWN_PER_GROUP);
  return (
    <section className="replay-happened" aria-label="What just happened">
      <header className="replay-happened__head">
        <h2 className="replay-happened__title">What just happened</h2>
        <button type="button" className="btn btn--ghost btn--sm" onClick={onDismiss}>Dismiss</button>
      </header>
      {groups.map((group) => (
        <div key={group.title}>
          <h3 className="replay-happened__group">{group.title}</h3>
          <EventList events={showAll ? group.events : group.events.slice(0, SHOWN_PER_GROUP)} />
        </div>
      ))}
      {isLong && (
        <button type="button" className="linklike" aria-expanded={showAll} onClick={() => setShowAll(!showAll)}>
          {showAll ? 'Show fewer' : `Show all ${events.length}`}
        </button>
      )}
    </section>
  );
}
