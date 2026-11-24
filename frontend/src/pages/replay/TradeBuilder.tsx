import { useState } from 'react';
import { counterReplayOffer, evaluateReplayTrade, getReplayTeam, proposeReplayTrade } from '../../api/replay';
import type {
  ReplayIrRow, ReplayOffer, ReplayPendingOffer, ReplayRosterRow, ReplaySummary, ReplayTradeBody, ReplayVerdict,
} from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { Skeleton, TableSkeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { useDebouncedValue } from '../../hooks/useDebouncedValue';
import { formatNumber } from '../../lib/format';
import { DropPicker, overLimit, PlayerNames, playerIds, toggleId, TradeVerdictView, type TradeActions } from './TradeParts';
import { ReplayPlayerCell, TeamSelect } from './shared';

const EVALUATE_DELAY_MS = 300;
const PROPOSE_KEY = 'propose';

/** Both teams' IR players are listed and tradable. */
type PickerRow = ReplayRosterRow | ReplayIrRow;

/** Why he can't be in a deal with the user (the rules in backend/services/replay_trades.py), or null. */
function blockedReason(row: PickerRow): string | null {
  return row.trade_locked ? 'traded in the last two weeks' : null;
}

function tradableIds(rows: readonly PickerRow[], ids: readonly number[]): number[] {
  return ids.filter((id) => rows.some((row) => row.player.id === id && blockedReason(row) === null));
}

interface RosterPickerProps {
  title: string;
  caption: string;
  /** "Give" or "Get": the start of each checkbox's name. */
  verb: string;
  rows: readonly PickerRow[];
  selected: readonly number[];
  max: number;
  onToggle: (playerId: number) => void;
  openPlayer: (playerId: number) => void;
}

/** One roster with a checkbox per player; trade-locked players and a full side can't be ticked. */
function RosterPicker({ title, caption, verb, rows, selected, max, onToggle, openPlayer }: RosterPickerProps) {
  return (
    <div>
      <h3 className="replay-trade__title">{title} <span className="replay-trade__count">{selected.length} of {max}</span></h3>
      <div className="table-wrap">
        <table className="data-table data-table--compact">
          <caption className="sr-only">{caption}</caption>
          <thead>
            <tr>
              <th scope="col"><span className="sr-only">{verb}</span></th>
              <th scope="col">Player</th>
              <th scope="col" className="num">Value</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const { player } = row;
              const reason = blockedReason(row);
              const isChecked = selected.includes(player.id);
              return (
                <tr key={player.id}>
                  <td>
                    <input type="checkbox" checked={isChecked} disabled={reason !== null || (!isChecked && selected.length >= max)}
                      aria-label={`${verb} ${player.name}${reason ? ` (can't be traded: ${reason})` : ''}`} onChange={() => onToggle(player.id)} />
                  </td>
                  <td className="td--player">
                    <ReplayPlayerCell player={player} info={{ out: row.out, trade_locked: row.trade_locked, buzz: row.buzz }} onOpen={openPlayer} />
                    {row.slot === 'IR' && <span className="status-badge status-badge--missing">On IR</span>}
                    {row.news && <p className="hint">{row.news.text}</p>}
                  </td>
                  <td className="num">{formatNumber(row.value)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

interface TradeBuilderProps {
  game: ReplaySummary;
  version: number;
  /** The user's roster now. */
  roster: readonly ReplayRosterRow[];
  /** The user's IR players now. */
  ir: readonly ReplayIrRow[];
  maxPlayers: number;
  /** A CPU offer being countered: the builder starts from its deal and sends the user's version back. */
  counterTo: ReplayPendingOffer | null;
  onCounterDone: () => void;
  /** From the URL (with=TEAM, want=PLAYER) when another screen links here. */
  initialPartner: number | null;
  initialWant: number | null;
  actions: TradeActions;
  openPlayer: (playerId: number) => void;
}

/** Pick a partner and players from both rosters; the verdict follows the selection, then propose it. */
export function TradeBuilder({ game, version, roster, ir, maxPlayers, counterTo, onCounterDone, initialPartner, initialWant, actions, openPlayer }: TradeBuilderProps) {
  const partners = game.teams.filter((team) => !team.is_user);
  const linked = partners.find((team) => team.team_id === (counterTo?.from_team.team_id ?? initialPartner));
  const [partnerId, setPartnerId] = useState(linked?.team_id ?? partners[0]?.team_id ?? 0);
  // A counter starts from the CPU's offer, written from its side: the user gives its "get" for its "give".
  const [give, setGive] = useState<number[]>(counterTo ? playerIds(counterTo.get) : []);
  const [get, setGet] = useState<number[]>(counterTo ? playerIds(counterTo.give) : linked && initialWant !== null ? [initialWant] : []);
  const [picked, setPicked] = useState<number[]>([]);
  const [declined, setDeclined] = useState<ReplayVerdict | null>(null);
  const [counter, setCounter] = useState<ReplayOffer | null>(null);
  const theirs = useAsync((signal) => getReplayTeam(game.id, { team_id: partnerId }, signal), [game.id, partnerId, version]);
  const partner = partners.find((team) => team.team_id === partnerId)?.name ?? 'That team';
  // While another partner's roster loads, the previous one must not be offered.
  const theirRows: PickerRow[] = theirs.data?.team.team_id === partnerId ? [...theirs.data.results, ...theirs.data.ir] : [];

  const myRows: PickerRow[] = [...roster, ...ir];
  const giveIds = tradableIds(myRows, give);
  const getIds = tradableIds(theirRows, get);
  const isIn = (rows: readonly PickerRow[]) => (id: number) => rows.some((row) => row.player.id === id);
  const freeIr = game.settings.ir_slots - ir.filter((row) => !giveIds.includes(row.player.id)).length;
  const toIr = Math.min(Math.max(freeIr, 0), getIds.filter(isIn(theirRows.filter((row) => row.slot === 'IR'))).length);
  const over = overLimit(roster, giveIds.filter(isIn(roster)).length, getIds.length - toIr, game.settings.roster_size);
  const spare = roster.filter((row) => !giveIds.includes(row.player.id));
  const drops = over > 0 ? picked.filter((id) => spare.some((row) => row.player.id === id)) : [];
  const isReady = giveIds.length > 0 && getIds.length > 0 && drops.length === Math.max(over, 0);
  const body: ReplayTradeBody | null = isReady ? { to_team: partnerId, give: giveIds, get: getIds, drops } : null;

  // Debounced, and useAsync aborts the request before: the latest selection wins.
  const bodyKey = body ? JSON.stringify(body) : '';
  const settledKey = useDebouncedValue(bodyKey, EVALUATE_DELAY_MS);
  const evaluation = useAsync(
    settledKey ? (signal) => evaluateReplayTrade(game.id, JSON.parse(settledKey) as ReplayTradeBody, signal) : null,
    [game.id, settledKey, version],
  );
  const isEvaluated = settledKey === bodyKey && evaluation.status === 'success';

  const change = (update: () => void) => {
    setDeclined(null);
    setCounter(null);
    update();
  };
  const propose = async () => {
    if (!body) return;
    const { to_team: _partner, ...deal } = body;
    const result = await actions.send(PROPOSE_KEY, () => (counterTo ? counterReplayOffer(game.id, counterTo.id, deal)
      : proposeReplayTrade(game.id, body)));
    if (!result) return;
    if (!result.executed) {
      setDeclined(result.verdict);
      setCounter(result.counter);
      if (counterTo) actions.announce({ text: `${partner} turned down your counter.${result.counter ? ' It countered back: see Offers for you.' : ''}`, isGood: false });
      return;
    }
    setGive([]);
    setGet([]);
    setPicked([]);
    actions.announce({ text: `Trade executed with ${partner}.`, isGood: true });
    if (counterTo) onCounterDone();         // the counter went through: back to a fresh builder
  };
  const chosen = partners.find((team) => team.team_id === partnerId);
  const tendency = chosen?.tendency;
  // A link asked for a player who has left that roster since (traded or dropped): say so instead of a silent blank.
  const wanted = counterTo === null && linked?.team_id === partnerId ? initialWant : null;
  const isWantGone = wanted !== null && theirRows.length > 0 && !theirRows.some((row) => row.player.id === wanted);
  const error = actions.errorFor(PROPOSE_KEY);

  return (
    <div className="replay-builder">
      <div className="weekly-controls">
        {/* A counter always goes back to the team that made the offer. */}
        <TeamSelect game={game} value={partnerId} label="Trade with" excludeUser disabled={counterTo !== null}
          onChange={(id) => change(() => { setPartnerId(id); setGet([]); })} />
      </div>
      {tendency && <p className="hint">{tendency}</p>}
      {chosen?.mood && <p className="game-mood"><span className="game-mood__label">Mood</span> {chosen.mood}</p>}
      <div role="status">
        {isWantGone && <p className="notice">That player is no longer on {partner}'s roster: pick from who they have now.</p>}
      </div>
      <div className="replay-trade__sides">
        <RosterPicker title="You give" caption="Your roster" verb="Give" rows={myRows} selected={giveIds} max={maxPlayers}
          onToggle={(id) => change(() => setGive(toggleId(giveIds, id)))} openPlayer={openPlayer} />
        <AsyncView state={theirs} loading={<TableSkeleton rows={8} columns={3} label={`Loading ${partner}'s roster…`} />} errorTitle={`Couldn't load ${partner}'s roster`}>
          {() => (theirRows.length === 0 ? <TableSkeleton rows={8} columns={3} label={`Loading ${partner}'s roster…`} /> : (
            <RosterPicker title="You get" caption={`${partner}'s roster`} verb="Get" rows={theirRows} selected={getIds} max={maxPlayers}
              onToggle={(id) => change(() => setGet(toggleId(getIds, id)))} openPlayer={openPlayer} />
          ))}
        </AsyncView>
      </div>
      <p className="hint">
        {`Up to ${maxPlayers} players a side. A player traded in the last two weeks can't be dealt; one on IR goes to your free IR slot, else your roster. `
          + 'Values are on a 0-1000 trade chart; an injured player counts for the games he is expected to play.'}
      </p>
      {counterTo && (
        <p className="hint">
          Change the deal and send it back to {counterTo.from_team.name}.{' '}
          <button type="button" className="btn btn--ghost btn--sm" onClick={onCounterDone}>Cancel counter</button>
        </p>
      )}
      {over > 0 && <DropPicker rows={spare} count={over} value={drops} onChange={(ids) => change(() => setPicked(ids))} />}
      {body ? (
        settledKey !== bodyKey ? <Skeleton lines={3} label="Checking the trade…" /> : (
          <AsyncView state={evaluation} loading={<Skeleton lines={3} label="Checking the trade…" />} errorTitle="This trade can't be made">
            {(data) => <TradeVerdictView verdict={data.verdict} partner={partner} />}
          </AsyncView>
        )
      ) : (
        <p className="hint">{giveIds.length > 0 && getIds.length > 0 ? 'Choose who to drop to see the verdict.' : 'Tick at least one player on each side to see the verdict.'}</p>
      )}
      <div className="replay-trade__actions">
        <button type="button" className="btn btn--primary" disabled={!isEvaluated || actions.isBusy} onClick={() => void propose()}>
          {actions.pendingKey === PROPOSE_KEY ? 'Proposing…' : counterTo ? 'Send counter' : 'Propose trade'}
        </button>
      </div>
      {error && <p className="error-text">{error}</p>}
      <div role="status">
        {declined && (
          <div className="replay-verdict">
            <p className="error-text"><strong>{partner} turned it down.</strong></p>
            <ul>{declined.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
            {declined.suggestion && <p className="hint">{declined.suggestion}</p>}
            {counter && (
              <p className="notice">
                <strong>{partner} countered:</strong> you give <PlayerNames players={counter.get} onOpen={openPlayer} /> for{' '}
                <PlayerNames players={counter.give} onOpen={openPlayer} />. Answer it under Offers for you.
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
