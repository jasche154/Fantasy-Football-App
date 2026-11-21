/**
 * Game Night building blocks shared by every replay screen: the score numeral, a fantasy team's badge and a
 * defense matchup chip. Skeleton: plain text renders; worker B adds the count-up, team colours and styles.
 */
import type { ReplayDefenseRating, ReplayPosition, ReplayTeamRef } from '../../api/types';
import { formatPoints, ordinal } from './shared';

interface ScoreNumberProps {
  value: number | null;
  size?: 'hero' | 'sm';
  /** Who or what the number is, for screen readers ("Your score"). */
  label: string;
}

export function ScoreNumber({ value, size = 'hero', label }: ScoreNumberProps) {
  return (
    <span className={size === 'sm' ? 'game-score game-score--sm' : 'game-score'}>
      <span className="sr-only">{`${label}: `}</span>
      {formatPoints(value)}
    </span>
  );
}

interface TeamBadgeProps {
  team: ReplayTeamRef;
  showTendency?: boolean;
}

export function TeamBadge({ team, showTendency = false }: TeamBadgeProps) {
  return (
    <span className="game-team" data-team={team.team_id}>
      <span className="game-team__name">{team.is_user ? `${team.name} (you)` : team.name}</span>
      {showTendency && !team.is_user && team.tendency ? <span className="game-team__tendency">{team.tendency}</span> : null}
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
