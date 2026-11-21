import type { ReplayMatchup, ReplayTeam } from '../../api/types';
import { ScoreNumber, TeamBadge } from './gameUi';
import { formatPoints, formatRecord, ordinal } from './shared';

function HeroSide({ side, matchup }: { side: ReplayTeam; matchup: ReplayMatchup }) {
  const isWinner = matchup.winner === side.team.team_id;
  return (
    <div className={`replay-hero__side${isWinner ? ' is-winner' : ''}`} data-team={side.team.team_id}>
      <p className="replay-hero__team">
        <TeamBadge team={side.team} />
        {isWinner && <span className="status-badge status-badge--ok">Winner</span>}
      </p>
      <p className="replay-hero__record">{formatRecord(side.record)} · {ordinal(side.record.rank)}</p>
      {/* Real points only once the week is played; before that the big number is the projection. */}
      <ScoreNumber value={matchup.played ? side.actual_total : side.projected_total} label={side.team.is_user ? 'Your score' : `${side.team.name} score`} />
      <p className="replay-hero__sub">{matchup.played ? `Projected ${formatPoints(side.projected_total)}` : 'Projected'}</p>
    </div>
  );
}

/** The game's scoreboard band: both teams and their scores, the user on the left. */
export function MatchupHero({ matchup, left, right }: { matchup: ReplayMatchup; left: ReplayTeam; right: ReplayTeam }) {
  const kind = matchup.label ?? (matchup.playoff ? 'Playoffs' : 'Regular season');
  const isTie = matchup.played && matchup.winner === null;
  return (
    <div className="replay-hero">
      <p className="replay-hero__label game-label">{kind} · Week {matchup.week} · {matchup.played ? (isTie ? 'Final, a tie' : 'Final') : 'Not played yet'}</p>
      <HeroSide side={left} matchup={matchup} />
      <span className="replay-hero__vs" aria-hidden="true">vs</span>
      <HeroSide side={right} matchup={matchup} />
    </div>
  );
}
