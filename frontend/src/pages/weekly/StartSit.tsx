import { useState } from 'react';
import { getWeeklyRankings } from '../../api/data';
import type { PlayerSummary, WeeklyPlayer } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { FlagChips, StatusBadge } from '../../components/FlagChips';
import { Icon } from '../../components/Icon';
import { PlayerSearch } from '../../components/PlayerSearch';
import { EmptyState, Skeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { formatNumber } from '../../lib/format';
import { useLeagueSettings } from '../../state/SettingsContext';
import { ExplainPanel } from './ExplainPanel';

const MAX_COMPARE = 4;
/** Projections closer than this are a coin flip; the recommendation then leans on floor or ceiling. */
const CLOSE_CALL = 1.5;

export type Strategy = 'balanced' | 'safe' | 'upside';

/** The start recommendation and why, from projection, then floor or ceiling on close calls. */
export function recommend(players: readonly WeeklyPlayer[], strategy: Strategy): { pick: WeeklyPlayer; reason: string } | null {
  if (players.length < 2) return null;
  const byProjection = [...players].sort((a, b) => b.projection - a.projection);
  const [first, second] = byProjection;
  const gap = first.projection - second.projection;
  if (gap >= CLOSE_CALL || strategy === 'balanced') {
    return { pick: first, reason: `Highest projection: ${formatNumber(first.projection)} vs ${formatNumber(second.projection)}.` };
  }
  const key = strategy === 'safe' ? 'floor' : 'ceiling';
  const close = byProjection.filter((p) => first.projection - p.projection < CLOSE_CALL);
  const pick = [...close].sort((a, b) => (b[key] ?? 0) - (a[key] ?? 0))[0];
  return {
    pick,
    reason: `Close call (within ${CLOSE_CALL} pts), so the ${strategy === 'safe' ? 'safer floor' : 'higher ceiling'} wins: ` +
      `${formatNumber(pick[key])} ${key}.`,
  };
}

function CompareCard({ player, isPick, week }: { player: WeeklyPlayer; isPick: boolean; week: number }) {
  const [showWhy, setShowWhy] = useState(false);
  return (
    <article className={isPick ? 'compare-card is-pick' : 'compare-card'} aria-label={player.player.name}>
      {isPick && <p className="compare-card__pick">Start</p>}
      <h3 className="compare-card__name">{player.player.name}</h3>
      <p className="compare-card__meta">
        {player.pos_rank} · {player.matchup.home ? 'vs' : '@'} {player.matchup.opponent}{' '}
        <StatusBadge status={player.status} playProb={player.play_prob} />
      </p>
      <p className="compare-card__projection">{formatNumber(player.projection)}</p>
      <p className="compare-card__range">Range {formatNumber(player.floor)} – {formatNumber(player.ceiling)}</p>
      {player.scenarios.map((s) => (
        <p key={s.teammate} className="compare-card__scenario">If {s.teammate} sits: {formatNumber(s.projection)}</p>
      ))}
      <FlagChips flags={player.flags} />
      <button type="button" className="btn btn--ghost btn--sm" aria-expanded={showWhy} onClick={() => setShowWhy((v) => !v)}>
        {showWhy ? 'Hide why' : 'Why?'}
      </button>
      {showWhy && <ExplainPanel playerId={player.player.id} week={week} />}
    </article>
  );
}

export function StartSit({ week }: { week: number | undefined }) {
  const { settings } = useLeagueSettings();
  const [players, setPlayers] = useState<readonly PlayerSummary[]>([]);
  const [strategy, setStrategy] = useState<Strategy>('balanced');
  const ids = players.map((p) => p.id);
  const data = useAsync(
    ids.length ? (signal) => getWeeklyRankings(settings, { week, ids, limit: MAX_COMPARE }, signal) : null,
    [ids.join(','), week, settings],
  );
  const results = data.data?.results ?? [];
  const advice = recommend(results, strategy);
  return (
    <section aria-label="Start or sit" className="start-sit">
      <div className="start-sit__controls">
        <PlayerSearch label="Add a player to compare" excludeIds={ids}
          onSelect={(player) => setPlayers((current) => [...current, player].slice(0, MAX_COMPARE))} />
        <div className="segmented" role="group" aria-label="Tiebreaker">
          {(['balanced', 'safe', 'upside'] as const).map((value) => (
            <button key={value} type="button" className={`segmented__option${strategy === value ? ' is-active' : ''}`}
              aria-pressed={strategy === value} onClick={() => setStrategy(value)}>
              {value === 'balanced' ? 'Most points' : value === 'safe' ? "I'm favored (safe)" : "I'm the underdog (upside)"}
            </button>
          ))}
        </div>
        {players.length > 0 && (
          <ul className="chip-list" aria-label="Players to compare">
            {players.map((p) => (
              <li key={p.id} className="chip">{p.name}
                <button type="button" className="chip__remove" aria-label={`Remove ${p.name}`}
                  onClick={() => setPlayers((current) => current.filter((x) => x.id !== p.id))}><Icon name="close" size={12} /></button>
              </li>
            ))}
          </ul>
        )}
      </div>
      {players.length === 0 ? (
        <EmptyState title="Compare up to four players.">Add the players you're deciding between.</EmptyState>
      ) : (
        <AsyncView state={data} loading={<Skeleton lines={5} label="Comparing…" />}
          isEmpty={(d) => d.results.length === 0}
          empty={<EmptyState title="No projections for these players this week.">They may be on bye or ruled out.</EmptyState>}>
          {(d) => (
            <>
              {advice && <p className="start-sit__advice" role="status"><strong>Start {advice.pick.player.name}.</strong> {advice.reason}</p>}
              <div className="compare-grid">
                {d.results.map((p) => <CompareCard key={p.player.id} player={p} isPick={advice?.pick.player.id === p.player.id} week={d.week} />)}
              </div>
            </>
          )}
        </AsyncView>
      )}
    </section>
  );
}
