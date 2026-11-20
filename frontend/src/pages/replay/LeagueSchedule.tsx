import { useState } from 'react';
import { getReplaySchedule } from '../../api/replay';
import type { ReplayScheduleRow } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { EmptyState, TableSkeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { EMPTY_VALUE } from '../../lib/format';
import { formatPoints, TeamSelect, type ReplayViewProps } from './shared';

const RESULT_TONES = { W: 'ok', L: 'out', T: 'missing' } as const;

function ScheduleRow({ row }: { row: ReplayScheduleRow }) {
  return (
    <tr>
      <td className="num">{row.week}</td>
      <td>
        {row.opponent?.name ?? (row.played ? 'No game' : 'To be decided')}
        {row.playoff && <span className="status-badge status-badge--missing replay-league__badge">Playoffs</span>}
      </td>
      <td>{row.result ? <span className={`status-badge status-badge--${RESULT_TONES[row.result]}`}>{row.result}</span> : EMPTY_VALUE}</td>
      {/* A week that hasn't been played never shows a score. */}
      <td className="num">{row.played ? `${formatPoints(row.points)} – ${formatPoints(row.opponent_points)}` : EMPTY_VALUE}</td>
    </tr>
  );
}

/** One team's season: every week's opponent, and the result once played. */
export function LeagueSchedule({ game, version }: ReplayViewProps) {
  const [teamId, setTeamId] = useState(game.user_team_id);
  const schedule = useAsync((signal) => getReplaySchedule(game.id, teamId, signal), [game.id, teamId, version]);
  return (
    <>
      <div className="weekly-controls">
        <TeamSelect game={game} value={teamId} onChange={setTeamId} />
      </div>
      <AsyncView state={schedule} loading={<TableSkeleton rows={10} columns={4} label="Loading the schedule…" />}
        errorTitle="Couldn't load the schedule" isEmpty={(data) => data.results.length === 0}
        empty={<EmptyState title="No schedule yet" />}>
        {(data) => (
          <div className="table-wrap">
            <table className="data-table data-table--compact">
              <caption className="sr-only">{data.team.name} schedule</caption>
              <thead>
                <tr>
                  <th scope="col" className="num">Week</th>
                  <th scope="col">Opponent</th>
                  <th scope="col">Result</th>
                  <th scope="col" className="num">Score</th>
                </tr>
              </thead>
              <tbody>{data.results.map((row) => <ScheduleRow key={row.week} row={row} />)}</tbody>
            </table>
          </div>
        )}
      </AsyncView>
    </>
  );
}
