import { useCallback, useEffect, useId, useState } from 'react';
import { getReplayClaims, getReplayPlayers, getReplayTeam, replayAddDrop, submitReplayClaim } from '../../api/replay';
import type { ReplayPlayerRef, ReplayPlayerSort, ReplayStatusFilter, ReplaySummary, ReplayWireRow } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { SelectField } from '../../components/SelectField';
import { nextSort, type SortState } from '../../components/SortableHeader';
import { EmptyState, TableSkeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { useDebouncedValue } from '../../hooks/useDebouncedValue';
import { ClaimsPanel } from './ClaimsPanel';
import { DIALOG_KEY, PlayersDropDialog, type DropTarget } from './PlayersDropDialog';
import { usePlayerMoves } from './PlayersMoves';
import { DEFAULT_SORT, sortDirection, statColumns, visibleSort, type StatsView } from './playerColumns';
import { addKey, PlayersTable } from './PlayersTable';
import { SEASON_STATS, StatsPicker, type StatsChoice } from './StatsPicker';
import { positionParam, PositionTabs, ReplayIrBlockedNotice, type ReplayPositionTab, type ReplayViewProps } from './shared';

const PAGE_SIZE = 25;
const SEARCH_DELAY_MS = 250;
const MAX_SEARCH_LENGTH = 60; // the backend's search limit

const STATUS_OPTIONS: readonly { value: ReplayStatusFilter; label: string }[] = [
  { value: 'available', label: 'Available' },
  { value: 'free_agent', label: 'Free agents' },
  { value: 'waivers', label: 'On waivers' },
  { value: 'rostered', label: 'Rostered' },
  { value: 'all', label: 'All players' },
];

/** Where the week stands for roster moves, worded from services/replay_waivers.py. */
function phaseNote({ phase, week, settings }: ReplaySummary): string {
  if (phase === 'complete') return 'The season is over, so rosters are final.';
  if (phase === 'waivers') return 'Every unrostered player is on waivers until you press “Sim to Wednesday”. Put in your claims now.';
  if (settings.waiver_mode === 'none') {
    return 'This league has no waivers: every unrostered player is a free agent, first come, first served. '
      + 'A player dropped this week can’t be added until next week.';
  }
  return `Week ${week}’s waivers have run, so every player still unrostered is a free agent: first come, first served. `
    + `A player dropped now waits on waivers, and once week ${week} is played everyone unrostered goes on waivers until the next “Sim to Wednesday”.`;
}

const VIEWS: readonly { value: StatsView; label: string }[] = [
  { value: 'box', label: 'Box score' },
  { value: 'advanced', label: 'Advanced' },
];

/** Box score or Advanced (usage and efficiency) columns; the rows and the stats span stay as they are. */
function ViewSwitch({ value, onChange }: { value: StatsView; onChange: (value: StatsView) => void }) {
  return (
    <div className="segmented replay-segmented replay-players__views" role="group" aria-label="Stat columns">
      {VIEWS.map((view) => (
        <button key={view.value} type="button" className={`segmented__option${view.value === value ? ' is-active' : ''}`}
          aria-pressed={view.value === value} onClick={() => onChange(view.value)}>
          {view.label}
        </button>
      ))}
    </div>
  );
}

interface PagerProps {
  total: number;
  offset: number;
  onChange: (offset: number) => void;
}

function Pager({ total, offset, onChange }: PagerProps) {
  if (total <= PAGE_SIZE && offset === 0) return null;
  const last = Math.min(offset + PAGE_SIZE, total);
  return (
    <nav className="replay-players__pager" aria-label="Player pages">
      <button type="button" className="btn btn--ghost btn--sm" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - PAGE_SIZE))}>Previous</button>
      <span className="hint">{Math.min(offset + 1, last)}–{last} of {total}</span>
      <button type="button" className="btn btn--ghost btn--sm" disabled={last >= total} onClick={() => onChange(offset + PAGE_SIZE)}>Next</button>
    </nav>
  );
}

/** Players: the waiver wire, free-agent adds and waiver claims. */
export function PlayersView({ game, version, act, isBusy, openPlayer }: ReplayViewProps) {
  const [tab, setTab] = useState<ReplayPositionTab>('ALL');
  const [status, setStatus] = useState<ReplayStatusFilter>('available');
  const [chosenSort, setSort] = useState<SortState<ReplayPlayerSort>>(DEFAULT_SORT);
  const [stats, setStats] = useState<StatsChoice>(SEASON_STATS);
  const [view, setView] = useState<StatsView>('box');
  const [search, setSearch] = useState('');
  const query = useDebouncedValue(search.trim(), SEARCH_DELAY_MS);
  const sentStatus = query ? 'all' : status;           // a name searches every player; clearing it restores Show
  // A week past the current one can't be asked for (a new game after a reload): fall back to the season.
  const shown = stats.span === 'week' && (stats.week ?? 0) > game.week ? SEASON_STATS : stats;
  const columns = statColumns(shown.span, shown.week, tab, view);
  const sort = visibleSort(chosenSort, columns);       // a hidden column never sorts the table
  if (sort !== chosenSort) setSort(sort);              // ...nor comes back when its column does
  const filterKey = [tab, sentStatus, sort.key, sort.direction, shown.span, shown.week, query].join('|');
  const [page, setPage] = useState({ filterKey, offset: 0 });
  const offset = page.filterKey === filterKey ? page.offset : 0;      // a new filter starts on its first page
  const [target, setTarget] = useState<DropTarget | null>(null);
  const closeDialog = useCallback(() => setTarget(null), []);
  const searchId = useId();

  const canMove = game.phase === 'open' || game.phase === 'waivers';
  const isFaab = game.settings.waiver_mode === 'faab';
  // In FAAB leagues the panel also shows every team's budget, so it stays after each waiver run.
  const showClaims = game.phase === 'waivers' || game.pending.claims > 0 || (isFaab && game.phase !== 'draft');
  const players = useAsync(
    (signal) => getReplayPlayers(game.id, {
      position: positionParam(tab), search: query, status: sentStatus, sort: sort.key, dir: sort.direction, stats: shown.span,
      stat_week: shown.week ?? undefined, limit: PAGE_SIZE, offset,
    }, signal).then((data) => ({ ...data, shown, tab })),  // headers come from their rows' span, never over a stale page
    [game.id, version, filterKey, offset],
  );
  const claims = useAsync(showClaims ? (signal) => getReplayClaims(game.id, signal) : null, [game.id, version]);
  const team = useAsync(canMove ? (signal) => getReplayTeam(game.id, {}, signal) : null, [game.id, version]);
  const claimedIds = new Set(claims.data?.results.map((claim) => claim.add.player.id));
  // No moves while anything reloads: the roster decides whether an add needs a drop, and a button on a
  // stale table or claim list would act on rows or a claim order that no longer exist.
  const moves = usePlayerMoves(act, isBusy || [players, claims, team].some((state) => state.status === 'loading'),
    `${game.week}:${game.phase}`);
  const earlierNoDrop = claims.data?.results.filter((claim) => claim.drop === null).length ?? 0;

  // An add or a drop can empty the last page: step back to the new last page.
  const emptyPage = players.status === 'success' && players.data.results.length === 0 && players.data.offset > 0;
  const lastOffset = players.data ? Math.max(0, Math.ceil(players.data.total / PAGE_SIZE) - 1) * PAGE_SIZE : 0;
  useEffect(() => {
    if (emptyPage) setPage({ filterKey, offset: lastOffset });
  }, [emptyPage, filterKey, lastOffset]);

  const add = (row: ReplayWireRow) => {
    const { id, name } = row.player;
    // Without a known open spot the dialog asks who to drop (and shows the roster's error when it failed to load).
    if (!team.data || team.data.results.length >= game.settings.roster_size) return setTarget({ row, kind: 'add' });
    void moves.run(addKey(id), () => replayAddDrop(game.id, { add: id, week: game.week }), `Added ${name}.`);
  };

  const confirm = async (drop: ReplayPlayerRef | null, bid?: number) => {
    if (!target) return;
    const { id, name } = target.row.player;
    const body = drop ? { add: id, drop: drop.id, week: game.week } : { add: id, week: game.week };
    const dropping = drop ? `, dropping ${drop.name}` : '';
    const forBid = bid === undefined ? '' : ` for $${bid}`;
    const sent = target.kind === 'claim'
      ? moves.run(DIALOG_KEY, () => submitReplayClaim(game.id, bid === undefined ? body : { ...body, bid }),
        `Claim for ${name} is in${forBid}${dropping}. It is settled on “Sim to Wednesday”.`)
      : moves.run(DIALOG_KEY, () => replayAddDrop(game.id, body), `Added ${name}${dropping}.`);
    if (await sent) setTarget(null);
  };

  return (
    <>
      <p className="hint replay-players__note">{phaseNote(game)}</p>
      {showClaims && <ClaimsPanel gameId={game.id} claims={claims} moves={moves} openPlayer={openPlayer} />}
      <ReplayIrBlockedNotice game={game} />
      <div className="weekly-controls replay-players__filters">
        <PositionTabs value={tab} onChange={setTab} withFlex />
        <SelectField label="Show" isInline value={status} options={STATUS_OPTIONS} onChange={(value) => setStatus(value as ReplayStatusFilter)} />
        {query && <span className="hint replay-players__search-note" role="status">Searching all players</span>}
        <StatsPicker game={game} value={shown} onChange={setStats} />
        <ViewSwitch value={view} onChange={setView} />
        <div className="field field--inline replay-players__search">
          <label className="field__label" htmlFor={searchId}>Search</label>
          <input id={searchId} type="search" className="input" placeholder="Player name" maxLength={MAX_SEARCH_LENGTH} value={search} onChange={(event) => setSearch(event.target.value)} />
        </div>
      </div>
      <p ref={moves.noticeRef} tabIndex={-1} className="success-text replay-players__notice" role="status">{moves.notice}</p>
      <AsyncView state={players} loading={<TableSkeleton rows={10} columns={6} label="Loading players…" />} errorTitle="Couldn't load the players">
        {(data) => (
          <>
            {data.results.length === 0
              ? <EmptyState title="No players match">{query ? 'Try another position or name.' : 'Try another position, status or name.'}</EmptyState>
              : (
                <PlayersTable game={game} week={data.week} rows={data.results} columns={statColumns(data.shown.span, data.shown.week, data.tab, view)} sort={sort}
                  onSort={(key) => setSort(nextSort(sort, key, sortDirection))} claimedIds={claimedIds} moves={moves}
                  onAdd={add} onClaim={(row) => setTarget({ row, kind: 'claim' })} openPlayer={openPlayer} />
              )}
            <Pager total={data.total} offset={data.offset} onChange={(next) => setPage({ filterKey, offset: next })} />
          </>
        )}
      </AsyncView>
      {target && <PlayersDropDialog target={target} game={game} team={team} earlierNoDrop={earlierNoDrop} moves={moves}
        bidding={isFaab && target.kind === 'claim' ? { left: claims.data?.faab?.left ?? null } : undefined}
        onConfirm={(drop, bid) => void confirm(drop, bid)} onClose={closeDialog} />}
    </>
  );
}
