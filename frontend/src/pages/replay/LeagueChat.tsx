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
  // The pressed button turns off at either end: keep focus on the pager, on the button that still goes somewhere.
  const turn = (next: number, isEnd: boolean) => {
    setPaging({ version, offset: next });
    if (isEnd) (next === 0 ? olderRef : newerRef).current?.focus();
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
              onClick={() => turn(Math.max(0, data.offset - PAGE_SIZE), data.offset <= PAGE_SIZE)}>Newer</button>
            <span className="hint" role="status">
              {data.results.length > 0 ? `${data.offset + 1}–${data.offset + data.results.length} of ${data.total}` : `0 of ${data.total}`}
            </span>
            <button ref={olderRef} type="button" className="btn btn--ghost btn--sm" disabled={data.offset + data.results.length >= data.total}
              onClick={() => turn(data.offset + PAGE_SIZE, data.offset + 2 * PAGE_SIZE >= data.total)}>Older</button>
          </div>
        </>
      )}
    </AsyncView>
  );
}
