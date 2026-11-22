import { useId } from 'react';
import type { ReplayPlayerWeek, ReplayPosition } from '../../api/types';
import { EMPTY_VALUE } from '../../lib/format';
import { DefenseChip } from './gameUi';
import { boxColumns, boxText, type BoxColumn } from './playerColumns';
import { formatPoints, ReplayInjuryBadge, showsOut } from './shared';

/** "@ BUF", "BUF" or "BYE". */
export function opponentText(opponent: string | null, away: boolean, bye: boolean): string {
  if (bye) return 'BYE';
  if (!opponent) return EMPTY_VALUE;
  return away ? `@ ${opponent}` : opponent;
}

function WeekRow({ week, position, box }: { week: ReplayPlayerWeek; position: ReplayPosition; box: readonly BoxColumn[] }) {
  return (
    <tr>
      <th scope="row" className="num">{week.week}</th>
      <td className="replay-card__opp">
        {opponentText(week.opponent, week.away, week.bye)}
        {week.injury && <> <ReplayInjuryBadge injury={week.injury} /></>}
        {showsOut(week.out, week.injury) && <span className="status-badge status-badge--out replay-drawer__badge">Out</span>}
        {/* The week still to play shows how tough the matchup is, like the weeks after it. */}
        {!week.played && !week.bye && <DefenseChip rating={week.defense} position={position} />}
      </td>
      <td className="num">{formatPoints(week.projection)}</td>
      {/* A week that hasn't been played never shows a result. */}
      <td className="num replay-card__pts">{week.played ? formatPoints(week.points) : EMPTY_VALUE}</td>
      {box.map(({ key }) => <td key={key} className="num">{boxText(week.played ? week.box : null, key)}</td>)}
    </tr>
  );
}

interface PlayerGameLogProps {
  name: string;
  position: ReplayPosition;
  weeks: readonly ReplayPlayerWeek[];
}

/** Weeks 1..current: the pre-game projection, the real points and his box score (played weeks only). */
export function PlayerGameLog({ name, position, weeks }: PlayerGameLogProps) {
  const headingId = useId();
  const box = boxColumns(position);
  return (
    <section className="replay-card__section" aria-labelledby={headingId}>
      <h3 id={headingId} className="game-subhead replay-card__subhead">Game log</h3>
      <div className="table-wrap">
        <table className="data-table data-table--compact replay-card__table">
          <caption className="sr-only">{name} game log</caption>
          <thead>
            <tr>
              <th scope="col" className="num">Wk</th>
              <th scope="col">Opp</th>
              <th scope="col" className="num"><abbr title="Pre-game projection">Proj</abbr></th>
              <th scope="col" className="num"><abbr title="Fantasy points scored">Pts</abbr></th>
              {box.map(({ key, label, title }) => <th key={key} scope="col" className="num"><abbr title={title}>{label}</abbr></th>)}
            </tr>
          </thead>
          <tbody>{weeks.map((week) => <WeekRow key={week.week} week={week} position={position} box={box} />)}</tbody>
        </table>
      </div>
      <p className="hint">Proj is the pre-game projection. Pts and the box score show played weeks only; a dash means he did not play.</p>
    </section>
  );
}
