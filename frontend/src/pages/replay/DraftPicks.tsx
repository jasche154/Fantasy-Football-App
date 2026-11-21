import { useState } from 'react';
import type { ReplayDraft, ReplayDraftPick, ReplayPosition, ReplaySettings } from '../../api/types';
import { Card } from '../../components/Card';
import { ReplayPlayerCell, ReplayPositionChip } from './shared';

const POSITIONS: readonly ReplayPosition[] = ['QB', 'RB', 'WR', 'TE', 'K', 'DST'];

interface DraftRosterProps {
  roster: ReplayDraft['roster'];
  settings: ReplaySettings;
  openPlayer: (playerId: number) => void;
}

/** The user's picks by position, each against how many of that position start. */
export function DraftRoster({ roster, settings, openPlayer }: DraftRosterProps) {
  const drafted = POSITIONS.reduce((count, position) => count + roster[position].length, 0);
  const lineup = Object.entries(settings.starters).map(([slot, count]) => `${count} ${slot}`).join(', ');
  return (
    <Card title="My roster" eyebrow={`${drafted} of ${settings.roster_size} picks made`}>
      <dl className="replay-draft__roster">
        {POSITIONS.map((position) => (
          <div key={position}>
            <dt>
              <ReplayPositionChip position={position} /> {roster[position].length} drafted
              <span className="replay-draft__starts"> · {settings.starters[position] ?? 0} start</span>
            </dt>
            {roster[position].length === 0 && <dd className="muted">None yet</dd>}
            {roster[position].map((player) => (
              <dd key={player.id}>
                <button type="button" className="replay-player__name" onClick={() => openPlayer(player.id)}>{player.name}</button>
                {player.team && <span className="player-cell__team"> {player.team}</span>}
              </dd>
            ))}
          </div>
        ))}
      </dl>
      <p className="hint">Lineup: {lineup} and {settings.bench} bench spots.</p>
    </Card>
  );
}

/** "3.05": round 3, the 5th pick of that round. */
function pickLabel(pick: ReplayDraftPick, teams: number): string {
  return `${pick.round}.${String(pick.overall - (pick.round - 1) * teams).padStart(2, '0')}`;
}

function PickLog({ draft, openPlayer }: DraftPicksProps) {
  if (draft.picks.length === 0) return <p className="hint">No picks yet.</p>;
  return (
    <div className="table-wrap replay-draft__scroll">
      <table className="data-table data-table--compact">
        <caption className="sr-only">Picks so far, most recent first</caption>
        <thead>
          <tr>
            <th scope="col" className="num">Rd</th>
            <th scope="col" className="num">Pick</th>
            <th scope="col">Team</th>
            <th scope="col">Player</th>
          </tr>
        </thead>
        <tbody>
          {[...draft.picks].reverse().map((pick) => (
            <tr key={pick.overall} className={pick.team.is_user ? 'row--mine replay-draft__mine' : undefined}>
              <td className="num">{pick.round}</td>
              <td className="num">{pickLabel(pick, draft.order.length)}</td>
              <td>{pick.team.name}{pick.team.is_user && <> <span className="sr-only">(you)</span></>}</td>
              <td className="td--player"><ReplayPlayerCell player={pick.player} onOpen={openPlayer} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Teams across (in first-round order), rounds down; each cell is that team's pick in that round. */
function PickGrid({ draft, openPlayer }: DraftPicksProps) {
  const taken = new Map(draft.picks.map((pick) => [`${pick.round}:${pick.team.team_id}`, pick]));
  const rounds = Array.from({ length: draft.rounds }, (_, index) => index + 1);
  return (
    <div className="table-wrap replay-draft__scroll">
      <table className="data-table data-table--compact replay-draft__grid">
        <caption className="sr-only">Draft grid: teams across, rounds down</caption>
        <thead>
          <tr>
            <th scope="col" className="num">Rd</th>
            {draft.order.map((team) => (
              <th key={team.team_id} scope="col" className={team.is_user ? 'replay-draft__mine' : undefined}>
                {team.name}{team.is_user && ' (you)'}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rounds.map((round) => (
            <tr key={round}>
              <th scope="row" className="num">{round}</th>
              {draft.order.map((team) => {
                const pick = taken.get(`${round}:${team.team_id}`);
                const isNext = round === draft.round && team.team_id === draft.on_the_clock;
                return (
                  <td key={team.team_id} className={team.is_user ? 'replay-draft__mine' : undefined}>
                    {pick && (
                      <>
                        <button type="button" className="replay-player__name" onClick={() => openPlayer(pick.player.id)}>{pick.player.name}</button>
                        <span className="replay-draft__cell-meta">{pickLabel(pick, draft.order.length)} · {pick.player.position}</span>
                      </>
                    )}
                    {!pick && isNext && <span className="status-badge status-badge--ok">On the clock</span>}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface DraftPicksProps {
  draft: ReplayDraft;
  openPlayer: (playerId: number) => void;
}

/** Every pick made so far: a log (most recent first) or the snake grid. */
export function DraftPicks({ draft, openPlayer }: DraftPicksProps) {
  const [isGrid, setIsGrid] = useState(false);
  const toggle = (
    <div className="segmented" role="group" aria-label="Show picks as">
      {[false, true].map((grid) => (
        <button key={String(grid)} type="button" aria-pressed={grid === isGrid} onClick={() => setIsGrid(grid)}
          className={`segmented__option${grid === isGrid ? ' is-active' : ''}`}>
          {grid ? 'Grid' : 'Log'}
        </button>
      ))}
    </div>
  );
  return (
    <Card title="Picks" eyebrow={`${draft.picks.length} of ${draft.total_picks} made`} actions={toggle}>
      {isGrid ? <PickGrid draft={draft} openPlayer={openPlayer} /> : <PickLog draft={draft} openPlayer={openPlayer} />}
    </Card>
  );
}
