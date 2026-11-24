import { useEffect, useRef, useState } from 'react';
import { getReplayChat } from '../../api/replay';
import { AsyncView } from '../../components/AsyncView';
import { EmptyState, Skeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { markChatSeen, useChatReading } from './chatSeen';
import { TeamBadge } from './gameUi';
import { eventText } from './LeagueActivity';
import type { ReplayViewProps } from './shared';

const PAGE_SIZE = 25;

/** The league chat, newest first: the CPU managers talk after each week and around trades; the user reads. */
export function LeagueChat({ game, version, openPlayer }: ReplayViewProps) {
  // The page belongs to a game version: once the game moves on, the feed starts again from the newest lines.
  const [paging, setPaging] = useState({ version, offset: 0 });
  const offset = paging.version === version ? paging.offset : 0;
  const feed = useAsync((signal) => getReplayChat(game.id, { limit: PAGE_SIZE, offset }, signal), [game.id, offset, version]);
  const total = feed.status === 'success' ? feed.data.total : 0;
  useChatReading(game.id);
  useEffect(() => {
    if (total > 0) markChatSeen(game.id, total);
  }, [game.id, total]);
  const newerRef = useRef<HTMLButtonElement>(null);
  const olderRef = useRef<HTMLButtonElement>(null);
  // After a pager press, once the new page shows: if the pressed button turned off at an end, focus the other
  // one (until then both buttons still reflect the old page). Never on first load or when a new week resets paging.
  const pressed = useRef<{ version: number; offset: number; older: boolean } | null>(null);
  const shownOffset = feed.status === 'success' ? feed.data.offset : null;
  useEffect(() => {
    const press = pressed.current;
    if (!press || press.version !== version || press.offset !== shownOffset) return;
    pressed.current = null;
    const [button, other] = press.older ? [olderRef, newerRef] : [newerRef, olderRef];
    if (button.current?.disabled) other.current?.focus();
  }, [shownOffset, version]);
  const turn = (next: number, older: boolean) => {
    pressed.current = { version, offset: next, older };
    setPaging({ version, offset: next });
  };
  return (
    <AsyncView state={feed} loading={<Skeleton lines={8} label="Loading the chat…" />} errorTitle="Couldn't load the chat"
      isEmpty={(data) => data.total === 0}
      empty={<EmptyState title="Quiet so far">The managers start talking once the first week is played.</EmptyState>}>
      {(data) => (
        <>
          <ol className="game-chat" aria-label="League chat, newest first">
            {data.results.map((line) => (
              <li key={line.id} className={line.to_user ? 'game-chat__line game-chat__line--to-you' : 'game-chat__line'}>
                <p className="game-chat__meta">
                  <TeamBadge team={line.team} />
                  <span className="game-chat__week">Wk {line.week}</span>
                  {line.to_user && <span className="game-chat__to">to you</span>}
                </p>
                <p className="game-chat__bubble" data-team={line.team.team_id}>{eventText(line, openPlayer)}</p>
              </li>
            ))}
          </ol>
          <div className="replay-pager">
            <button ref={newerRef} type="button" className="btn btn--ghost btn--sm" disabled={data.offset === 0}
              onClick={() => turn(Math.max(0, data.offset - PAGE_SIZE), false)}>Newer</button>
            <span className="hint" role="status">
              {data.results.length > 0 ? `${data.offset + 1}–${data.offset + data.results.length} of ${data.total}` : `0 of ${data.total}`}
            </span>
            <button ref={olderRef} type="button" className="btn btn--ghost btn--sm" disabled={data.offset + data.results.length >= data.total}
              onClick={() => turn(data.offset + PAGE_SIZE, true)}>Older</button>
          </div>
        </>
      )}
    </AsyncView>
  );
}
