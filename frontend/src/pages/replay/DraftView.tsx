import { useRef, useState, type ReactNode, type RefObject } from 'react';
import { getReplayDraft, replayDraftAuto, replayDraftPick } from '../../api/replay';
import type { ReplayActionResult, ReplayDraft, ReplayDraftSort } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { nextSort, type SortDirection, type SortState } from '../../components/SortableHeader';
import { Skeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { useDebouncedValue } from '../../hooks/useDebouncedValue';
import { DraftBoard } from './DraftBoard';
import { DraftPicks, DraftRoster } from './DraftPicks';
import { adpSource, ConfirmButton, positionParam, type ReplayPositionTab, type ReplayViewProps } from './shared';

const PAGE_SIZE = 50;
// shortcut: "Show more" grows one request up to the backend's MAX_PAGE; past that the user filters or searches.
// Upgrade to offset paging if a board of 200 rows ever isn't enough.
const MAX_PAGE_SIZE = 200;
const SEARCH_DELAY_MS = 250;

const DEFAULT_SORT: SortState<ReplayDraftSort> = { key: 'rank', direction: 'asc' };
/** Best first: low rank and ADP, high projection. */
const sortDirection = (key: ReplayDraftSort): SortDirection => (key === 'projection' ? 'desc' : 'asc');

/** The control pressed last: an auto button or a player's Draft button. */
type Pressed = 'auto' | 'rest' | number;

interface ClockProps {
  draft: ReplayDraft;
  statusRef: RefObject<HTMLDivElement | null>;
  children: ReactNode;
}

/** Which pick is up and whose it is, with the auto-draft buttons. */
function Clock({ draft, statusRef, children }: ClockProps) {
  const { round, overall } = draft;
  const team = draft.order.find((candidate) => candidate.team_id === draft.on_the_clock);
  return (
    <div className={draft.is_user_turn ? 'replay-draft__clock replay-draft__clock--mine' : 'replay-draft__clock'}>
      <div ref={statusRef} className="replay-draft__status" role="status" tabIndex={-1}>
        {round === null || overall === null ? (
          <p className="replay-draft__turn">The draft is complete</p>
        ) : (
          <>
            <p className="replay-draft__pick">
              Round {round}, pick {overall - (round - 1) * draft.order.length}
              <span className="muted"> · {overall} of {draft.total_picks} overall</span>
            </p>
            <p className="replay-draft__turn">{draft.is_user_turn ? "You're on the clock" : `${team?.name ?? 'Another team'} is on the clock`}</p>
          </>
        )}
      </div>
      <div className="replay-draft__actions">{children}</div>
    </div>
  );
}

/** The draft room: the board, the picks so far and the user's roster (shown alone while phase is "draft"). */
export function DraftView({ game, version, act, isBusy, actError, openPlayer }: ReplayViewProps) {
  const [position, setPosition] = useState<ReplayPositionTab>('ALL');
  const [search, setSearch] = useState('');
  const [limit, setLimit] = useState(PAGE_SIZE);
  const [sort, setSort] = useState(DEFAULT_SORT);
  const [pressed, setPressed] = useState<Pressed | null>(null);
  const statusRef = useRef<HTMLDivElement>(null);
  const query = useDebouncedValue(search.trim(), SEARCH_DELAY_MS);
  const draft = useAsync(
    (signal) => getReplayDraft(game.id, {
      position: positionParam(position), search: query || undefined, sort: sort.key, direction: sort.direction, limit,
    }, signal),
    [game.id, version, position, query, sort, limit],
  );

  const run = async (what: Pressed, request: () => Promise<ReplayActionResult>) => {
    setPressed(what);
    if (!(await act(request))) return draft.reload();       // a refused pick usually means the board was stale
    if (typeof what === 'number') statusRef.current?.focus();      // his row is about to go: keep focus in the room
  };
  const isPending = (what: Pressed) => isBusy && pressed === what;

  return (
    <AsyncView state={draft} loading={<Skeleton lines={8} label="Loading the draft room…" />} errorTitle="Couldn't load the draft room">
      {(data) => {
        // Nothing is pressable while a pick is being sent or the board is catching up with the last one.
        const canAct = data.is_user_turn && !isBusy && draft.status !== 'loading';
        const hasMore = data.results.length < data.total && limit < MAX_PAGE_SIZE;
        return (
          <div className="replay-draft">
            <Clock draft={data} statusRef={statusRef}>
              <button type="button" className="btn btn--ghost" disabled={!canAct} onClick={() => run('auto', () => replayDraftAuto(game.id, undefined, data.overall))}>
                {isPending('auto') ? 'Picking…' : 'Auto-pick for me'}
              </button>
              <ConfirmButton className="btn btn--ghost" disabled={!canAct} confirmLabel="Yes, draft the rest for me"
                label={isPending('rest') ? 'Drafting the rest…' : 'Auto-draft the rest'}
                onConfirm={() => run('rest', () => replayDraftAuto(game.id, true, data.overall))} />
            </Clock>
            <DraftBoard draft={data} rankSource={game.rank_source} adpNote={adpSource(game.kind, game.start_week > 1)} position={position} search={search} sort={sort} canDraft={canAct} isBusy={isBusy}
              pressedId={typeof pressed === 'number' ? pressed : null} error={actError} openPlayer={openPlayer}
              onPosition={(next) => { setPosition(next); setLimit(PAGE_SIZE); }}
              onSearch={(next) => { setSearch(next); setLimit(PAGE_SIZE); }}
              onSort={(key) => { setSort(nextSort(sort, key, sortDirection)); setLimit(PAGE_SIZE); }}
              onShowMore={hasMore ? () => setLimit(limit + PAGE_SIZE) : undefined}
              onDraft={(playerId) => run(playerId, () => replayDraftPick(game.id, playerId, data.overall))} />
            <div className="replay-draft__side">
              <DraftRoster roster={data.roster} settings={game.settings} openPlayer={openPlayer} />
              <DraftPicks draft={data} openPlayer={openPlayer} />
            </div>
          </div>
        );
      }}
    </AsyncView>
  );
}
