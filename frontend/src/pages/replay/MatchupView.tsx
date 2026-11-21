import { useRef, useState } from 'react';
import { getReplayMatchup, getReplayScoreboard } from '../../api/replay';
import type { ReplayMatchup, ReplaySettings, ReplayTeam } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { SelectField } from '../../components/SelectField';
import { Skeleton, TableSkeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { MatchupScoreboard } from './MatchupScoreboard';
import { formatPoints, formatRecord, ordinal, ReplayPlayerCell, type ReplayViewProps } from './shared';
import { lineupSpots, weekOptions, type LineupSpot } from './teamLineup';

function teamName(side: ReplayTeam): string {
  return side.team.is_user ? `${side.team.name} (you)` : side.team.name;
}

function HeadlineSide({ side, matchup }: { side: ReplayTeam; matchup: ReplayMatchup }) {
  const isWinner = matchup.winner === side.team.team_id;
  return (
    <div className={`replay-headline__side${isWinner ? ' is-winner' : ''}`}>
      <p className="replay-headline__team">
        <strong>{teamName(side)}</strong>
        {isWinner && <span className="status-badge status-badge--ok">Winner</span>}
      </p>
      <p className="hint">{formatRecord(side.record)} · {ordinal(side.record.rank)}</p>
      {/* Real points only once the week is played; before that the big number is the projection. */}
      <p className="replay-headline__score">{formatPoints(matchup.played ? side.actual_total : side.projected_total)}</p>
      <p className="hint">{matchup.played ? `Projected ${formatPoints(side.projected_total)}` : 'Projected'}</p>
    </div>
  );
}

interface SideTableProps {
  side: ReplayTeam;
  spots: readonly LineupSpot[];
  openPlayer: (playerId: number) => void;
}

/** One team's lineup, in the My Team column order on both sides so a stacked phone layout reads the same twice. */
function SideTable({ side, spots, openPlayer }: SideTableProps) {
  return (
    <div className="table-wrap">
      <table className="data-table data-table--compact replay-faceoff__table">
        <caption className="sr-only">{side.team.name} lineup, week {side.week}</caption>
        <thead>
          <tr>
            <th scope="col">Slot</th>
            <th scope="col">Player</th>
            <th scope="col" className="num">Proj</th>
            <th scope="col" className="num">Pts</th>
          </tr>
        </thead>
        <tbody>
          {spots.map(({ key, slot, row }) => (
            <tr key={key} className={slot === 'BN' ? 'replay-faceoff__bench' : undefined}>
              <td className="td--pos">{slot}</td>
              <td className="td--player">
                {row ? <ReplayPlayerCell player={row.player} info={row} onOpen={openPlayer} /> : <span className="muted">Empty</span>}
              </td>
              <td className="num">{formatPoints(row?.projection)}</td>
              <td className="num">{formatPoints(row?.points)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface FaceOffProps {
  matchup: ReplayMatchup;
  starters: ReplaySettings['starters'];
  /** The user's own game was asked for, but he has none this week (a bye): the server sent the week's first game. */
  isUserIdle: boolean;
  openPlayer: (playerId: number) => void;
}

function FaceOff({ matchup, starters, isUserIdle, openPlayer }: FaceOffProps) {
  // The user's team goes on the left when it is his game.
  const [left, right] = matchup.away.team.is_user ? [matchup.away, matchup.home] : [matchup.home, matchup.away];
  const kind = matchup.label ?? (matchup.playoff ? 'Playoffs' : 'Regular season');
  const isTie = matchup.played && matchup.winner === null;
  return (
    <>
      <p className="sr-only" role="status">Showing {left.team.name} against {right.team.name}, week {matchup.week}.</p>
      {isUserIdle && <p className="hint">No game this week for your team. Here is another game; pick any from the scoreboard below.</p>}
      <div className="replay-headline">
        <p className="replay-headline__label">{kind} · Week {matchup.week} · {matchup.played ? (isTie ? 'Final, a tie' : 'Final') : 'Not played yet'}</p>
        <HeadlineSide side={left} matchup={matchup} />
        <span className="replay-headline__vs" aria-hidden="true">vs</span>
        <HeadlineSide side={right} matchup={matchup} />
      </div>
      <div className="replay-faceoff">
        <SideTable side={left} spots={lineupSpots(left.results, starters)} openPlayer={openPlayer} />
        <SideTable side={right} spots={lineupSpots(right.results, starters)} openPlayer={openPlayer} />
      </div>
    </>
  );
}

/** Matchup: one game of a week face to face, and the week's scoreboard to pick another from. */
export function MatchupView({ game, version, openPlayer }: ReplayViewProps) {
  const [pickedWeek, setPickedWeek] = useState<number | null>(null);       // null: follow the current week
  // A game picked from the scoreboard, kept with its week: another week (picked, or reached by playing) is the user's game again.
  const [picked, setPicked] = useState<{ week: number; teamId: number } | null>(null);
  const faceOffRef = useRef<HTMLElement>(null);
  const week = Math.min(pickedWeek ?? game.week, game.week);
  const teamId = picked?.week === week ? picked.teamId : undefined;
  const matchup = useAsync((signal) => getReplayMatchup(game.id, { week, team_id: teamId }, signal), [game.id, week, teamId, version]);
  const scoreboard = useAsync((signal) => getReplayScoreboard(game.id, week, signal), [game.id, week, version]);
  const changeWeek = (value: string) => setPickedWeek(Number(value) === game.week ? null : Number(value));
  const show = (id: number) => {
    setPicked({ week, teamId: id });
    faceOffRef.current?.focus();        // the game is above the scoreboard: bring it into view
  };
  return (
    <>
      <div className="weekly-controls replay-team__controls">
        <SelectField label="Week" isInline value={week} options={weekOptions(game.week)} onChange={changeWeek} />
      </div>
      <section ref={faceOffRef} className="replay-matchup" tabIndex={-1} aria-label="Face-off">
        <AsyncView state={matchup} loading={<TableSkeleton rows={9} columns={4} label="Loading the matchup…" />} errorTitle="Couldn't load this matchup">
          {(data) => (
            <FaceOff matchup={data} starters={game.settings.starters} openPlayer={openPlayer}
              isUserIdle={data.week === week && teamId === undefined && !data.home.team.is_user && !data.away.team.is_user} />
          )}
        </AsyncView>
      </section>
      <AsyncView state={scoreboard} loading={<Skeleton lines={4} label="Loading the scoreboard…" />} errorTitle="Couldn't load the scoreboard">
        {(board) => <MatchupScoreboard board={board} shownTeamId={matchup.data?.home.team.team_id} onShow={show} />}
      </AsyncView>
    </>
  );
}
