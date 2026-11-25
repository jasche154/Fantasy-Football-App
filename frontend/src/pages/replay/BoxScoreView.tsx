import { useEffect, useId, useRef } from 'react';
import { getReplayBox } from '../../api/replay';
import type { ReplayBoxLine, ReplayBoxPlayer, ReplayBoxScore, ReplayBoxTeam } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { Skeleton } from '../../components/StatusViews';
import { Tabs } from '../../components/Tabs';
import { useAsync } from '../../hooks/useAsync';
import { formatPoints, ReplayPositionChip } from './shared';

type Column = { key: keyof ReplayBoxLine; label: string; title: string };

/** ESPN's sections: who shows in each, and its columns (the box has no attempts or completions). */
const SECTIONS: readonly { title: string; has: (box: ReplayBoxLine) => boolean; columns: readonly Column[] }[] = [
  { title: 'Passing', has: (b) => b.pass_yds !== 0 || b.pass_td > 0 || b.pass_int > 0, columns: [
    { key: 'pass_yds', label: 'YDS', title: 'Passing yards' }, { key: 'pass_td', label: 'TD', title: 'Passing touchdowns' },
    { key: 'pass_int', label: 'INT', title: 'Interceptions' }] },
  { title: 'Rushing', has: (b) => b.carries > 0, columns: [
    { key: 'carries', label: 'CAR', title: 'Carries' }, { key: 'rush_yds', label: 'YDS', title: 'Rushing yards' },
    { key: 'rush_td', label: 'TD', title: 'Rushing touchdowns' }] },
  { title: 'Receiving', has: (b) => b.targets > 0 || b.rec > 0, columns: [
    { key: 'rec', label: 'REC', title: 'Receptions' }, { key: 'targets', label: 'TGT', title: 'Targets' },
    { key: 'rec_yds', label: 'YDS', title: 'Receiving yards' }, { key: 'rec_td', label: 'TD', title: 'Receiving touchdowns' }] },
];

function StatTable({ team, title, columns, players }: { team: string; title: string; columns: readonly Column[]; players: ReplayBoxPlayer[] }) {
  if (players.length === 0) return null;
  return (
    <div className="table-wrap">
      <table className="data-table data-table--compact replay-box__table">
        <caption className="replay-box__caption">{title}<span className="sr-only">, {team}</span></caption>
        <thead>
          <tr>
            <th scope="col">Player</th>
            {columns.map(({ key, label, title: full }) => <th key={key} scope="col" className="num"><abbr title={full}>{label}</abbr></th>)}
          </tr>
        </thead>
        <tbody>
          {players.map((player) => (
            <tr key={player.id}>
              <th scope="row">{player.name}</th>
              {columns.map(({ key }) => <td key={key} className="num">{player.box?.[key]}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TeamBox({ side }: { side: ReplayBoxTeam }) {
  if (side.players.length === 0) return <p className="hint">No player from this team is in the player pool.</p>;
  return (
    <div className="replay-box__team">
      {SECTIONS.map(({ title, has, columns }) => (
        <StatTable key={title} team={side.team} title={title} columns={columns}
          players={side.players.filter((player) => player.box && has(player.box))} />
      ))}
      <div className="table-wrap">
        <table className="data-table data-table--compact replay-box__table">
          <caption className="replay-box__caption">Fantasy points<span className="sr-only">, {side.team}</span></caption>
          <thead><tr><th scope="col">Player</th><th scope="col">Pos</th><th scope="col" className="num">Pts</th></tr></thead>
          <tbody>
            {side.players.map((player) => (
              <tr key={player.id}>
                <th scope="row">{player.name}</th>
                <td><ReplayPositionChip position={player.position} /></td>
                <td className="num replay-card__pts">{formatPoints(player.points)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** "JAX 20 @ TEN 17", or the matchup alone when the season has no final score. */
function finalText([away, home]: ReplayBoxScore['teams']): string {
  if (away.score === null || home.score === null) return `${away.team} @ ${home.team}`;
  return `${away.team} ${away.score} @ ${home.team} ${home.score}`;
}

interface BoxScoreViewProps {
  gameId: number;
  week: number;
  /** The team whose tab opens first (the player's team). */
  team: string;
  backLabel: string;
  onBack: () => void;
}

/** A played NFL game's box score: the final score, then one tab per team with every player's line. */
export function BoxScoreView({ gameId, week, team, backLabel, onBack }: BoxScoreViewProps) {
  const box = useAsync((signal) => getReplayBox(gameId, week, team, signal), [gameId, week, team]);
  const heading = useRef<HTMLHeadingElement>(null);
  const headingId = useId();
  useEffect(() => heading.current?.focus(), []);
  return (
    <section className="replay-box" aria-labelledby={headingId}>
      <button type="button" className="btn btn--ghost btn--sm replay-box__back" onClick={onBack}>← {backLabel}</button>
      <h3 id={headingId} ref={heading} tabIndex={-1} className="game-subhead replay-card__subhead">Week {week} box score</h3>
      <AsyncView state={box} loading={<Skeleton lines={6} label="Loading box score…" />} errorTitle="Couldn't load this box score">
        {(data) => (
          <>
            <p className="replay-box__final">
              {finalText(data.teams)}
              {data.teams.some((side) => side.score === null) ? <span className="hint"> · final score not available</span> : <span className="hint"> · Final</span>}
            </p>
            <Tabs label={`Week ${week} teams`} className="replay-adv__tabs" initial={Math.max(0, data.teams.findIndex((side) => side.team === team))}
              tabs={data.teams.map((side) => ({ id: side.team, label: side.team, panel: <TeamBox side={side} /> }))} />
          </>
        )}
      </AsyncView>
    </section>
  );
}
