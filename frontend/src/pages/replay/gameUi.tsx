/**
 * Game Night building blocks shared by every replay screen: the score numeral, a fantasy team's badge and a
 * defense matchup chip. Styles live in styles/game.css (.game-score, .game-team, .game-chip, [data-team]).
 */
import { useLayoutEffect, useRef, useState } from 'react';
import type { ReplayDefenseRating, ReplayPosition, ReplayTeamRef } from '../../api/types';
import { formatPoints, ordinal } from './shared';

const COUNT_UP_MS = 600;

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/** The number to draw: `target` at once, or (with `enabled`) counting up to it from the last value shown (0 at first). */
function useCountUp(target: number | null, enabled: boolean): number | null {
  const run = enabled && target !== null && !prefersReducedMotion();
  const shown = useRef(0);
  const [frame, setFrame] = useState<number | null>(run ? 0 : null);
  useLayoutEffect(() => {
    if (!run || target === null) return undefined;
    const origin = shown.current;
    const start = performance.now();
    let id = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / COUNT_UP_MS);
      const eased = 1 - (1 - t) ** 3;
      if (t < 1) {
        setFrame(origin + (target - origin) * eased);
        id = requestAnimationFrame(tick);
      } else {
        setFrame(null);
      }
    };
    setFrame(origin);
    id = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(id);
      shown.current = target;
    };
  }, [run, target]);
  return run && frame !== null ? frame : target;
}

interface ScoreNumberProps {
  value: number | null;
  size?: 'hero' | 'sm';
  /** Who or what the number is, for screen readers ("Your score"). */
  label: string;
  /** Count up to the value (from 0, then from the last value) over 600ms: pass it when a week's result arrives. */
  countUp?: boolean;
}

/** A scoreboard numeral. Screen readers get the label and the final value at once; only the digits animate. */
export function ScoreNumber({ value, size = 'hero', label, countUp = false }: ScoreNumberProps) {
  const shown = useCountUp(value, countUp);
  return (
    <span className={size === 'sm' ? 'game-score game-score--sm' : 'game-score'}>
      <span className="sr-only">{`${label}: ${formatPoints(value)}`}</span>
      <span aria-hidden="true">{formatPoints(shown)}</span>
    </span>
  );
}

interface TeamBadgeProps {
  team: ReplayTeamRef;
  showTendency?: boolean;
}

/** A fantasy team's name with its colour dot (team colours by team_id; the user's team in the accent). */
export function TeamBadge({ team, showTendency = false }: TeamBadgeProps) {
  const tendency = showTendency && !team.is_user ? team.tendency : null;
  return (
    <span className={team.is_user ? 'game-team game-team--you' : 'game-team'} data-team={team.team_id}>
      <span className="game-team__dot" aria-hidden="true" />
      <span className="game-team__name">{team.is_user ? `${team.name} (you)` : team.name}</span>
      {tendency && <span className="game-team__tendency">{tendency}</span>}
    </span>
  );
}

const TIER_LABEL: Record<ReplayDefenseRating['tier'], string> = { easy: 'Easy', neutral: 'Neutral', tough: 'Tough' };

interface DefenseChipProps {
  rating: ReplayDefenseRating | null;
  position: ReplayPosition;
}

/** "Easy · 3rd": rank 1 gives up the most to the position. Nothing when the rating is unknown. */
export function DefenseChip({ rating, position }: DefenseChipProps) {
  if (!rating) return null;
  const title = `${TIER_LABEL[rating.tier]} matchup: ranks ${ordinal(rating.rank)} of ${rating.of} in points allowed to ${position}s (${rating.allowed} a game).`;
  return (
    <span className={`game-chip game-chip--${rating.tier}`} title={title}>
      {`${TIER_LABEL[rating.tier]} · ${ordinal(rating.rank)}`}
      <span className="sr-only">{` ${title}`}</span>
    </span>
  );
}
