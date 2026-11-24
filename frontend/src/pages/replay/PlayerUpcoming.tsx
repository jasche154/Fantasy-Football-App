import { useId } from 'react';
import type { ReplayPlayerDetail, ReplayPosition, ReplayUpcomingWeek } from '../../api/types';
import { EMPTY_VALUE } from '../../lib/format';
import { DefenseChip } from './gameUi';
import { isSimWeek, opponentText, SimLegend, SimWeekMark } from './PlayerGameLog';
import { formatPoints } from './shared';

/** "Matchup ratings: 6 weeks this season, blended with last season." */
export function defenseBasisText({ weeks, prior }: ReplayPlayerDetail['defense_basis']): string {
  if (weeks === 0) return prior ? 'Matchup ratings: last season only, until games are played.' : 'Matchup ratings start once games are played.';
  const count = weeks === 1 ? '1 week' : `${weeks} weeks`;
  return `Matchup ratings: ${count} this season${prior ? ', blended with last season' : ''}.`;
}

function estimateText(week: ReplayUpcomingWeek): string {
  if (week.bye) return EMPTY_VALUE;
  if (week.out_expected) return 'Out';
  return formatPoints(week.estimate);
}

interface PlayerUpcomingProps {
  name: string;
  position: ReplayPosition;
  upcoming: readonly ReplayUpcomingWeek[];
  basis: ReplayPlayerDetail['defense_basis'];
  /** The league's first simulated week: later weeks are marked Sim. */
  simFrom?: number | null;
}

/** The weeks after this one: opponent, how that defense treats his position, and an estimate (never a projection). */
export function PlayerUpcoming({ name, position, upcoming, basis, simFrom = null }: PlayerUpcomingProps) {
  const headingId = useId();
  if (upcoming.length === 0) return null;
  return (
    <section className="replay-card__section" aria-labelledby={headingId}>
      <h3 id={headingId} className="game-subhead replay-card__subhead">Upcoming</h3>
      <div className="table-wrap">
        <table className="data-table data-table--compact replay-card__table">
          <caption className="sr-only">{name} upcoming weeks</caption>
          <thead>
            <tr>
              <th scope="col" className="num">Wk</th>
              <th scope="col">Opp</th>
              <th scope="col">Matchup</th>
              <th scope="col" className="num"><abbr title="Estimate: rest-of-season rate adjusted for the opponent">Est.</abbr></th>
            </tr>
          </thead>
          <tbody>
            {upcoming.map((week) => (
              <tr key={week.week}>
                <th scope="row" className="num">{week.week}{isSimWeek(week.week, simFrom) && <> <SimWeekMark /></>}</th>
                <td>{opponentText(week.opponent, week.away, week.bye)}</td>
                <td>{week.bye ? null : <DefenseChip rating={week.defense} position={position} />}</td>
                <td className="num">{estimateText(week)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="hint">Est. is his rest-of-season rate adjusted for the opponent, not a site projection. {defenseBasisText(basis)}</p>
      <SimLegend weeks={upcoming} simFrom={simFrom} />
    </section>
  );
}
