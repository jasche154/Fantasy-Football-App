import type { ReplaySummary } from '../../api/types';
import { Icon, type IconName } from '../../components/Icon';
import { useChatUnread } from './chatSeen';
import { UnreadDot } from './gameUi';
import { REPLAY_VIEWS, viewHash, type ReplayView } from './shared';

const ICONS: Record<ReplayView, IconName> = { team: 'team', matchup: 'stats', players: 'search', trades: 'trade', league: 'rankings' };

/** The game's sections: a tab strip under the scorebug on wide screens, a bottom dock on phones. Pending counts as badges. */
export function GameNav({ game, view }: { game: ReplaySummary; view: ReplayView }) {
  const badges: Partial<Record<ReplayView, { count: number; noun: string }>> = {
    players: { count: game.pending.claims, noun: 'pending claims' },
    trades: { count: game.pending.offers, noun: 'offers to answer' },
  };
  const hasUnread = useChatUnread(game);
  return (
    <nav className="game-nav" aria-label="League sections">
      <ul className="game-nav__list">
        {REPLAY_VIEWS.map((tab) => {
          const badge = badges[tab.value];
          return (
            <li key={tab.value}>
              <a className="game-nav__tab" href={viewHash(game.id, tab.value)} aria-current={tab.value === view ? 'page' : undefined}>
                <Icon name={ICONS[tab.value]} />
                <span className="game-nav__label">{tab.label}</span>
                {badge && badge.count > 0 && (
                  <span className="game-nav__badge">{badge.count}<span className="sr-only"> {badge.noun}</span></span>
                )}
                {tab.value === 'league' && hasUnread && <UnreadDot />}
              </a>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
