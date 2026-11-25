/** Replay League trades: the pieces the offers, the builder and the history share. */
import { Fragment, useState } from 'react';
import type { ReplayPlayerRef, ReplayPricedPlayer, ReplayRosterRow, ReplayVerdict } from '../../api/types';
import { formatInteger, formatNumber, formatSigned, pluralize } from '../../lib/format';
import { BalanceMeter } from '../trade/BalanceMeter';
import { VERDICT_COPY } from '../trade/TradeResultView';
import { ReplayHypeStar, ReplayPlayerCell, useKeyedAct, type KeyedAct, type ReplayAct } from './shared';

export interface TradeNotice {
  text: string;
  isGood: boolean;
}

/** The shared keyed `act`, plus the outcome of the last answer or proposal. */
export interface TradeActions extends KeyedAct {
  /** Cleared when the next action starts. */
  notice: TradeNotice | null;
  announce: (notice: TradeNotice) => void;
}

export function useTradeActions(act: ReplayAct, isBusy: boolean): TradeActions {
  const keyed = useKeyedAct(act, isBusy);
  const [notice, setNotice] = useState<TradeNotice | null>(null);
  const send: KeyedAct['send'] = (key, run) => {
    setNotice(null);
    return keyed.send(key, run);
  };
  return { ...keyed, send, notice, announce: setNotice };
}

export function toggleId(ids: readonly number[], id: number): number[] {
  return ids.includes(id) ? ids.filter((other) => other !== id) : [...ids, id];
}

export function playerIds(players: readonly ReplayPlayerRef[]): number[] {
  return players.map((player) => player.id);
}

/** How many players the user must drop after sending `sends` and receiving `receives` (0 or less: none). */
export function overLimit(roster: readonly ReplayRosterRow[], sends: number, receives: number, rosterSize: number): number {
  return roster.length - sends + receives - rosterSize;
}

/** Names that open the player drawer, separated by commas. */
export function PlayerNames({ players, onOpen }: { players: readonly ReplayPlayerRef[]; onOpen: (playerId: number) => void }) {
  return (
    <>
      {players.map((player, index) => (
        <Fragment key={player.id}>
          {index > 0 && ', '}
          <button type="button" className="replay-player__name" onClick={() => onOpen(player.id)}>{player.name}</button>
          {player.hype && <> <ReplayHypeStar player={player} /></>}
        </Fragment>
      ))}
    </>
  );
}

interface TradeSideProps {
  title: string;
  /** `value` is left out when there is no verdict to price him. */
  players: readonly (Pick<ReplayPricedPlayer, 'player'> & { value?: number })[];
  onOpen: (playerId: number) => void;
}

/** One side of a deal: each player and what he counts for. */
export function TradeSide({ title, players, onOpen }: TradeSideProps) {
  return (
    <div>
      <h3 className="replay-trade__title">{title}</h3>
      <ul className="replay-trade__players">
        {players.map(({ player, value }) => (
          <li key={player.id}>
            <ReplayPlayerCell player={player} onOpen={onOpen} />
            {value !== undefined && <span className="replay-trade__value">{formatInteger(value)}<span className="sr-only"> value</span></span>}
          </li>
        ))}
      </ul>
    </div>
  );
}

function names(players: readonly ReplayPlayerRef[]): string {
  return players.map((player) => player.name).join(', ');
}

interface TradeVerdictViewProps {
  verdict: ReplayVerdict;
  /** The CPU team's name. */
  partner: string;
  /** A CPU's own offer: it only needs a line when it no longer stands. */
  isOffer?: boolean;
}

/** A lineup loss (rest-of-season points) big enough to call out next to a trade-value headline. */
export const LINEUP_LOSS_WARNING = 10;

function sumValues(players: readonly { value: number }[]): number {
  return players.reduce((total, player) => total + player.value, 0);
}

/** A verdict from the user's side: the balance, both values, the lineup gains, drops and why a CPU says no. */
export function TradeVerdictView({ verdict, partner, isOffer = false }: TradeVerdictViewProps) {
  const copy = VERDICT_COPY[verdict.fair_label];
  const isAdjusted = Math.abs(sumValues(verdict.give) - verdict.value_give) > 0.05
    || Math.abs(sumValues(verdict.get) - verdict.value_get) > 0.05;
  return (
    <div className="replay-verdict">
      <BalanceMeter percentDifference={verdict.percent} />
      <p>
        <span className={`status-badge verdict--${copy.tone}`}><span aria-hidden="true">{copy.symbol} </span>{copy.label}</span>{' '}
        on trade value. You give {formatInteger(verdict.value_give)} value, get {formatInteger(verdict.value_get)}.
        {isAdjusted && ' The totals count the best player in the deal a little extra and take off anyone you would drop.'}
      </p>
      <p>
        Points added to each best lineup over the rest of the season: you {formatSigned(verdict.your_gain)}, {partner}{' '}
        {formatSigned(verdict.their_gain)}.
      </p>
      {verdict.your_gain <= -LINEUP_LOSS_WARNING && (
        <p className="error-text">
          Careful: whatever the value says, this costs your best lineup {formatNumber(-verdict.your_gain)} points over the rest of the season.
        </p>
      )}
      {verdict.your_drops.length > 0 && <p>You would drop {names(verdict.your_drops)}.</p>}
      {verdict.their_drops.length > 0 && <p>{partner} would drop {names(verdict.their_drops)}.</p>}
      {verdict.accepted
        ? !isOffer && <p className="success-text">{partner} would accept this trade.</p>
        : <p className="error-text">{isOffer ? `This offer no longer works for ${partner}:` : `${partner} would turn this down:`}</p>}
      {verdict.reasons.length > 0 && <ul>{verdict.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>}
      {verdict.suggestion && <p className="hint">{verdict.suggestion}</p>}
    </div>
  );
}

interface DropPickerProps {
  /** The user's players who are not part of the deal. */
  rows: readonly ReplayRosterRow[];
  /** Exactly how many must go. */
  count: number;
  value: readonly number[];
  onChange: (ids: number[]) => void;
}

/** The user's own choice of who to cut when a deal overfills the roster. */
export function DropPicker({ rows, count, value, onChange }: DropPickerProps) {
  return (
    <fieldset className="fieldset replay-drops">
      <legend>Choose {pluralize(count, 'player')} to drop ({value.length} of {count} chosen)</legend>
      {rows.map((row) => {
        const isChecked = value.includes(row.player.id);
        return (
          <label key={row.player.id} className="checkbox">
            <input type="checkbox" checked={isChecked} disabled={!isChecked && value.length >= count}
              onChange={() => onChange(toggleId(value, row.player.id))} />
            <span>Drop {row.player.name} <span className="muted">({row.player.position}, value {formatInteger(row.value)})</span></span>
          </label>
        );
      })}
    </fieldset>
  );
}
