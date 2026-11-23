import { useId } from 'react';
import type { ReplayDraft, ReplayDraftSort, ReplaySummary } from '../../api/types';
import { Card } from '../../components/Card';
import { SortableHeader, type SortState } from '../../components/SortableHeader';
import { EmptyState } from '../../components/StatusViews';
import { formatInteger, formatNumber } from '../../lib/format';
import { formatPoints, PositionTabs, ReplayPlayerCell, type ReplayPositionTab } from './shared';

/** The backend's `search` limit. */
const MAX_SEARCH_LENGTH = 60;
const SORT_NAMES: Record<ReplayDraftSort, string> = { rank: 'rank', adp: 'ADP', projection: 'projection' };
const RANK_NOTES: Record<ReplaySummary['rank_source'], string> = {
  experts: 'Rank: preseason expert consensus (ADP order where no expert rank).',
  model: 'Rank: ADP order — no expert ranks for this season.',
};
const PROJ_NOTE = 'Proj: raw points a game, not adjusted for position.';

interface DraftBoardProps {
  draft: ReplayDraft;
  /** Where the Rank column comes from: expert ranks, or ADP order when the season has none. */
  rankSource: ReplaySummary['rank_source'];
  position: ReplayPositionTab;
  onPosition: (position: ReplayPositionTab) => void;
  search: string;
  onSearch: (search: string) => void;
  sort: SortState<ReplayDraftSort>;
  onSort: (key: ReplayDraftSort) => void;
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

/** Who is left, sorted by rank, ADP or projection, with a Draft button per row that works on the user's turn. */
export function DraftBoard({
  draft, rankSource, position, onPosition, search, onSearch, sort, onSort, onShowMore, canDraft, isBusy, pressedId, error, onDraft,
  openPlayer,
}: DraftBoardProps) {
  const searchId = useId();
  const isOver = draft.round === null;
  return (
    <Card title="Available players" eyebrow={`By ${SORT_NAMES[sort.key]}`}>
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
            <caption className="sr-only">Available players</caption>
            <thead>
              <tr>
                <SortableHeader columnKey="rank" sort={sort} onSort={onSort}>Rank</SortableHeader>
                <th scope="col" className="num" title="The round his rank puts him in">Rd</th>
                <th scope="col">Player</th>
                <th scope="col">Pos rank</th>
                <SortableHeader columnKey="adp" sort={sort} onSort={onSort} title="Preseason average draft position">ADP</SortableHeader>
                <SortableHeader columnKey="projection" sort={sort} onSort={onSort}>Proj</SortableHeader>
                <th scope="col"><span className="sr-only">Draft</span></th>
              </tr>
            </thead>
            <tbody>
              {draft.results.map((row) => {
                const isPressed = row.player.id === pressedId;
                return (
                  <tr key={row.player.id}>
                    <td className="num">{formatInteger(row.pre_rank)}</td>
                    <td className="num">{formatInteger(row.round)}</td>
                    <td className="td--player"><ReplayPlayerCell player={row.player} info={{ buzz: row.buzz }} onOpen={openPlayer} /></td>
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
          <p className="hint">{RANK_NOTES[rankSource]} {PROJ_NOTE}</p>
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
