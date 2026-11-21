import { useEffect, useRef, useState } from 'react';
import { getReplayTeam, replayDrop, replayIrActivate, replayIrPlace, setReplayLineup } from '../../api/replay';
import type { ReplayPlayerRef, ReplayTeam } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { SelectField } from '../../components/SelectField';
import { TableSkeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { Chooser, DIALOG_KEY } from './PlayersDropDialog';
import { EDIT_PHASES, ReplayDrawer, ReplayIrBlockedNotice, TeamSelect, useKeyedAct, type ReplayViewProps } from './shared';
import { lineupSpots, lineupWarnings, swappedLineup, weekOptions, type LineupSpot } from './teamLineup';
import { TeamRosterTable } from './TeamRosterTable';

interface RosterProps extends Omit<ReplayViewProps, 'version' | 'actError'> {
  team: ReplayTeam;
  /** The roster is being reloaded: what is on screen may be about to change. */
  isStale: boolean;
}

function Roster({ team, isStale, game, act, isBusy, openPlayer }: RosterProps) {
  const [movingKey, setMovingKey] = useState<string | null>(null);
  // An object, so each drop is a new identity and refocuses the status line.
  const [dropped, setDropped] = useState<{ name: string } | null>(null);
  // The IR player being activated onto a full roster: the drop chooser is open.
  const [activating, setActivating] = useState<ReplayPlayerRef | null>(null);
  const statusRef = useRef<HTMLParagraphElement>(null);
  const keyed = useKeyedAct(act, isBusy);
  const { pendingKey: pending, failure } = keyed;
  const send: typeof keyed.send = (key, run) => {
    setDropped(null);
    return keyed.send(key, run);
  };
  const isCurrent = team.week === game.week && !team.played;
  const canEdit = team.team.is_user && isCurrent && EDIT_PHASES.includes(game.phase);
  const spots = lineupSpots(team.results, game.settings.starters);
  const source = (canEdit && spots.find((spot) => spot.key === movingKey && spot.row)) || null;
  const warnings = isCurrent ? lineupWarnings(spots) : [];
  const onIr = team.ir[0]?.player ?? null;

  useEffect(() => {
    if (movingKey === null) return undefined;
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setMovingKey(null); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [movingKey]);

  // The pressed Drop button leaves with its row: put focus on the line that says what happened.
  useEffect(() => {
    if (dropped) statusRef.current?.focus({ preventScroll: true });
  }, [dropped]);

  const drop = async (playerId: number) => {
    const name = [...team.results, ...team.ir].find((row) => row.player.id === playerId)?.player.name;
    if (await send(`drop:${playerId}`, () => replayDrop(game.id, playerId)) && name) setDropped({ name });
  };

  const move = (spot: LineupSpot) => {
    setDropped(null);
    setMovingKey(source || !spot.row ? null : spot.key);
    if (source && spot.key !== source.key) void send(`move:${spot.key}`, () => setReplayLineup(game.id, swappedLineup(spots, source, spot)));
  };

  const activate = (player: ReplayPlayerRef) => {
    if (team.results.length >= game.settings.roster_size) return setActivating(player);
    void send(`activate:${player.id}`, () => replayIrActivate(game.id, player.id));
  };

  const activateAndDrop = async (drop: ReplayPlayerRef | null) => {
    if (!activating || !drop) return;
    if (await send(DIALOG_KEY, () => replayIrActivate(game.id, activating.id, { drop: drop.id }))) setActivating(null);
  };

  const editing = canEdit ? {
    source, pending, isDisabled: isBusy || isStale, onMove: move,
    onDrop: (playerId: number) => void drop(playerId),
    onIr: (playerId: number) => void send(`ir:${playerId}`, () => (onIr
      ? replayIrActivate(game.id, onIr.id, { swap: playerId })
      : replayIrPlace(game.id, playerId))),
    onActivate: () => { if (onIr) activate(onIr); },
  } : undefined;

  return (
    <>
      {team.team.is_user && isCurrent && <ReplayIrBlockedNotice game={game} />}
      {isCurrent && !game.has_injuries && (
        <p className="notice" role="status">
          Injury reports are not loaded for the {game.season} season, so no tags are shown and the IR slot can&apos;t be used.
          Add them under New league on the Replay page.
        </p>
      )}
      {canEdit && (
        <div className="replay-lineup-status">
          <strong>{team.manual_lineup ? 'Lineup set by you' : 'Best lineup (auto)'}</strong>
          {team.manual_lineup && (
            <button type="button" className="btn btn--ghost btn--sm" disabled={isBusy || isStale}
              onClick={() => void send('reset', () => setReplayLineup(game.id, null))}>
              {pending === 'reset' ? 'Resetting…' : 'Reset to best lineup'}
            </button>
          )}
          <p ref={statusRef} tabIndex={-1} className="hint" role="status">
            {source?.row
              ? `Moving ${source.row.player.name}: choose a highlighted spot, or press Escape to cancel.`
              : `${dropped ? `Dropped ${dropped.name}. ` : ''}Press Move on a player, then choose the spot to swap him into.`}
          </p>
        </div>
      )}
      {/* The hub's alert announces the message; this copy sits next to the button that was pressed (the dialog shows its own). */}
      {failure && failure.key !== DIALOG_KEY && <p className="error-text">{failure.message}</p>}
      {warnings.length > 0 && (
        <ul className="notice replay-lineup-warnings" aria-label="Lineup warnings">
          {warnings.map((warning) => <li key={warning}>{warning}</li>)}
        </ul>
      )}
      <TeamRosterTable team={team} spots={spots} openPlayer={openPlayer} editing={editing} showIr={isCurrent} />
      <p className="hint">
        {team.played
          ? `What started in week ${team.week}, with the real points. A dash means he did not play.`
          : 'Proj is this week’s pre-game projection; Pts fills in once the week is played.'}
        {isCurrent && ' A player tagged O, IR or PUP can go on IR, which frees his roster spot.'}
      </p>
      {activating && (
        <ReplayDrawer eyebrow="Injured reserve" title={`Activate ${activating.name}`} onClose={() => setActivating(null)}>
          <Chooser target={{ row: { player: activating }, kind: 'activate' }} game={game} roster={team} earlierNoDrop={0} moves={keyed}
            onConfirm={(choice) => void activateAndDrop(choice)} onClose={() => setActivating(null)} />
        </ReplayDrawer>
      )}
    </>
  );
}

/** My Team: any team's roster for a week, and the user's own lineup, IR and drops in the current one. */
export function TeamView({ game, version, ...rest }: ReplayViewProps) {
  const [teamId, setTeamId] = useState(game.user_team_id);
  const [pickedWeek, setPickedWeek] = useState<number | null>(null);       // null: follow the current week
  const week = Math.min(pickedWeek ?? game.week, game.week);
  const roster = useAsync((signal) => getReplayTeam(game.id, { team_id: teamId, week }, signal), [game.id, teamId, week, version]);
  return (
    <>
      <div className="weekly-controls replay-team__controls">
        <TeamSelect game={game} value={teamId} onChange={setTeamId} />
        <SelectField label="Week" isInline value={week} options={weekOptions(game.week)}
          onChange={(value) => setPickedWeek(Number(value) === game.week ? null : Number(value))} />
      </div>
      <AsyncView state={roster} loading={<TableSkeleton rows={12} columns={8} label="Loading the roster…" />} errorTitle="Couldn't load this roster">
        {(team) => (
          <Roster key={`${team.team.team_id}-${team.week}`} team={team} isStale={roster.status === 'loading'} game={game} {...rest} />
        )}
      </AsyncView>
    </>
  );
}
