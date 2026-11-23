import { useEffect, useState } from 'react';
import { getReplayChat } from '../../api/replay';
import { AsyncView } from '../../components/AsyncView';
import { EmptyState, Skeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { markChatSeen } from './chatSeen';
import { TeamBadge } from './gameUi';
import { eventText } from './LeagueActivity';
import type { ReplayViewProps } from './shared';

const PAGE_SIZE = 25;

/** The league chat, newest first: the CPU managers talk after each week and around trades; the user reads. */
export function LeagueChat({ game, version, openPlayer }: ReplayViewProps) {
  const [offset, setOffset] = useState(0);
  const feed = useAsync((signal) => getReplayChat(game.id, { limit: PAGE_SIZE, offset }, signal), [game.id, offset, version]);
  const total = feed.status === 'success' ? feed.data.total : 0;
  useEffect(() => {
    if (total > 0) markChatSeen(game.id, total);
  }, [game.id, total]);
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
  );
}
