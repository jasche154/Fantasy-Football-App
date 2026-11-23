import { useState, type ReactNode } from 'react';
import { LeagueActivity } from './LeagueActivity';
import { LeaguePlayoffs } from './LeaguePlayoffs';
import { LeagueSchedule } from './LeagueSchedule';
import { LeagueScoreboard } from './LeagueScoreboard';
import { LeagueStandings } from './LeagueStandings';
import type { ReplayViewProps } from './shared';

const PAGES: readonly { label: string; Page: (props: ReplayViewProps) => ReactNode }[] = [
  { label: 'Standings', Page: LeagueStandings },
  { label: 'Playoffs', Page: LeaguePlayoffs },
  { label: 'Schedule', Page: LeagueSchedule },
  { label: 'Scoreboard', Page: LeagueScoreboard },
  { label: 'Activity', Page: LeagueActivity },
];

/** League: standings, the playoff bracket, scoreboard, schedule and activity. Opens on the bracket once the playoffs start. */
export function LeagueView(props: ReplayViewProps) {
  const { game } = props;
  const isPlayoffs = game.week > game.settings.regular_season_weeks || game.phase === 'complete';
  const [page, setPage] = useState(PAGES[isPlayoffs ? 1 : 0]);
  return (
    <div className="replay-league">
      <div className="segmented replay-segmented" role="group" aria-label="League pages">
        {PAGES.map((option) => (
          <button key={option.label} type="button" aria-pressed={option === page}
            className={`segmented__option${option === page ? ' is-active' : ''}`} onClick={() => setPage(option)}>
            {option.label}
          </button>
        ))}
      </div>
      <page.Page {...props} />
    </div>
  );
}
