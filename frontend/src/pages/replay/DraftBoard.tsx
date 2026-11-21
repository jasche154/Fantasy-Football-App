import { useId } from 'react';
import type { ReplayDraft } from '../../api/types';
import { Card } from '../../components/Card';
import { EmptyState } from '../../components/StatusViews';
import { formatNumber } from '../../lib/format';
import { formatPoints, PositionTabs, ReplayPlayerCell, type ReplayPositionTab } from './shared';

/** The backend's `search` limit. */
const MAX_SEARCH_LENGTH = 60;

interface DraftBoardProps {
  draft: ReplayDraft;
  position: ReplayPositionTab;
  onPosition: (position: ReplayPositionTab) => void;
  search: string;
  onSearch: (search: string) => void;
  /** Undefined when every match is shown or the page is as long as the server allows. */
  onShowMore?: () => void;
  /** The user's turn, and nothing in flight. */
  canDraft: boolean;
  isBusy: boolean;
  /** The player whose Draft button was pressed last: it shows the pending text, or `error` if the pick failed. */
  pressedId: number | null;
  error: string | null;
  onDraft: (playerId: number) => void;
  openPlayer: (playerId: number) => void;
}

/** Who is left, in draft (ADP) order, with a Draft button per row that works on the user's turn. */
export function DraftBoard({
  draft, position, onPosition, search, onSearch, onShowMore, canDraft, isBusy, pressedId, error, onDraft, openPlayer,
}: DraftBoardProps) {
  const searchId = useId();
  const isOver = draft.round === null;
  return (
    <Card title="Available players" eyebrow="In draft order">
      <div className="weekly-controls">
        <PositionTabs value={position} onChange={onPosition} withFlex />
        <div className="field field--inline">
          <label className="field__label" htmlFor={searchId}>Search</label>
          <input id={searchId} type="search" className="input" value={search} maxLength={MAX_SEARCH_LENGTH} placeholder="Player name"
            onChange={(event) => onSearch(event.target.value)} />
        </div>
      </div>
      {draft.results.length === 0 ? (
        <EmptyState title={isOver ? 'The draft is over' : 'No players match'}>
          {isOver ? 'Every pick has been made.' : 'Try another position or a shorter search.'}
        </EmptyState>
      ) : (
        <div className="table-wrap">
          <table className="data-table data-table--compact">
            <caption className="sr-only">Available players in draft order</caption>
            <thead>
              <tr>
                <th scope="col" className="num" title="Place among the players shown, in draft (ADP) order">#</th>
                <th scope="col">Player</th>
                <th scope="col">Pos rank</th>
                <th scope="col" className="num" title="Preseason average draft position">ADP</th>
                <th scope="col" className="num" title="Preseason projection, points a game">Proj</th>
                <th scope="col"><span className="sr-only">Draft</span></th>
              </tr>
            </thead>
            <tbody>
              {draft.results.map((row, index) => {
                const isPressed = row.player.id === pressedId;
                return (
                  <tr key={row.player.id}>
                    {/* The board always starts at offset 0, so this is the place in ADP order. */}
                    <td className="num">{index + 1}</td>
                    <td className="td--player"><ReplayPlayerCell player={row.player} onOpen={openPlayer} /></td>
                    <td>{row.pos_rank}</td>
                    <td className="num">{formatNumber(row.adp)}</td>
                    <td className="num">{formatPoints(row.projection)}</td>
                    <td className="replay-draft__action">
                      <button type="button" className="btn btn--primary btn--sm" disabled={!canDraft}
                        onClick={() => onDraft(row.player.id)}>
                        {isPressed && isBusy ? 'Drafting…' : 'Draft'} <span className="sr-only">{row.player.name}</span>
                      </button>
                      {/* The hub's alert announces it; this copy is for a row scrolled far below that alert. */}
                      {isPressed && !isBusy && error && <span className="error-text replay-draft__error">{error}</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <div className="weekly-controls">
        <p className="hint" role="status">
          Showing {draft.results.length} of {draft.total} players
          {!onShowMore && draft.results.length < draft.total && ': search or filter by position to find the rest'}
        </p>
        {onShowMore && <button type="button" className="btn btn--ghost btn--sm" onClick={onShowMore}>Show more</button>}
      </div>
      <p className="hint attribution">ADP: Fantasy Football Calculator</p>
    </Card>
  );
}
