import { useRef, useState } from 'react';
import { getReplayMatchup, getReplayScoreboard } from '../../api/replay';
import type { ReplayMatchup, ReplaySettings, ReplayTeam } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { SelectField } from '../../components/SelectField';
import { Skeleton, TableSkeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { MatchupHero } from './MatchupHero';
import { MatchupScoreboard } from './MatchupScoreboard';
import { formatPoints, ReplayPlayerCell, type ReplayViewProps } from './shared';
import { BENCH_NOT_SAVED, lineupSpots, slotLabel, weekOptions, type LineupSpot } from './teamLineup';

interface SideTableProps {
  side: ReplayTeam;
  spots: readonly LineupSpot[];
  openPlayer: (playerId: number) => void;
}

/** One team's lineup, in the My Team column order on both sides so a stacked phone layout reads the same twice. */
function SideTable({ side, spots, openPlayer }: SideTableProps) {
  const starters = spots.filter((spot) => spot.slot !== 'BN');
  const bench = spots.filter((spot) => spot.slot === 'BN');
  const spotRow = ({ key, slot, row }: LineupSpot) => (
    <tr key={key}>
      <td className="td--pos"><span className="replay-slot">{slotLabel(slot)}</span></td>
      <td className="td--player">
        {row ? <ReplayPlayerCell player={row.player} info={row} onOpen={openPlayer} /> : <span className="muted">Empty</span>}
      </td>
      <td className="num">{formatPoints(row?.projection)}</td>
      <td className="num">{formatPoints(row?.points)}</td>
    </tr>
  );
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
        <tbody>{starters.map(spotRow)}</tbody>
        {bench.length > 0 && (
          <tbody className="replay-faceoff__bench">
            <tr>
              <th scope="rowgroup" colSpan={4} className="replay-faceoff__benchhead"><span className="game-subhead">Bench</span></th>
            </tr>
            {bench.map(spotRow)}
            {side.bench_total !== null && (
              <tr className="replay-faceoff__total">
                <th scope="row" colSpan={3}>Bench total</th>
                <td className="num">{formatPoints(side.bench_total)}</td>
              </tr>
            )}
          </tbody>
        )}
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
  const isBenchMissing = matchup.played && !(left.bench_recorded && right.bench_recorded);
  return (
    <>
      <p className="sr-only" role="status">Showing {left.team.name} against {right.team.name}, week {matchup.week}.</p>
      {isUserIdle && <p className="hint">No game this week for your team. Here is another game; pick any from the scoreboard below.</p>}
      <MatchupHero matchup={matchup} left={left} right={right} />
      <div className="replay-faceoff">
        <SideTable side={left} spots={lineupSpots(left.results, starters)} openPlayer={openPlayer} />
        <SideTable side={right} spots={lineupSpots(right.results, starters)} openPlayer={openPlayer} />
      </div>
      {isBenchMissing && <p className="hint">{BENCH_NOT_SAVED}</p>}
    </>
  );
}

/** Matchup: one game of a week face to face, and the week's scoreboard to pick another from. */
export function MatchupView({ game, version, openPlayer }: ReplayViewProps) {
  const [pickedWeek, setPickedWeek] = useState<number | null>(null);       // null: follow the current week
  const [isAutoPinned, setIsAutoPinned] = useState(false);                  // pickedWeek is the week just played, not the user's pick
  // A game picked from the scoreboard, kept with its week: another week (picked, or reached by playing) is the user's game again.
  const [picked, setPicked] = useState<{ week: number; teamId: number } | null>(null);
  const faceOffRef = useRef<HTMLElement>(null);
  const [seenWeek, setSeenWeek] = useState(game.week);
  if (seenWeek !== game.week) {
    setSeenWeek(game.week);
    // Playing the week on screen moves the game on: stay on it, back on the user's game, so its final lands here.
    // Each later play moves an automatic pin along; a week the user picked stays put.
    if ((pickedWeek === null || isAutoPinned) && game.week === seenWeek + 1) {
      setPickedWeek(seenWeek);
      setIsAutoPinned(true);
      setPicked(null);
    }
  }
  const week = Math.min(pickedWeek ?? game.week, game.week);
  const teamId = picked?.week === week ? picked.teamId : undefined;
  const matchup = useAsync((signal) => getReplayMatchup(game.id, { week, team_id: teamId }, signal), [game.id, week, teamId, version]);
  const scoreboard = useAsync((signal) => getReplayScoreboard(game.id, week, signal), [game.id, week, version]);
  const changeWeek = (value: string) => {
    setPickedWeek(Number(value) === game.week ? null : Number(value));
    setIsAutoPinned(false);
  };
  const show = (id: number) => {
    setPicked({ week, teamId: id });
    faceOffRef.current?.focus();        // the game is above the scoreboard: bring it into view
  };
  return (
    <>
      <div className="weekly-controls replay-team__controls">
        <SelectField label="Week" isInline value={week} options={weekOptions(game.week, game.start_week)} onChange={changeWeek} />
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
