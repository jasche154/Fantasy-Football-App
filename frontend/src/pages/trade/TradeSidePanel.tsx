import { useId, useRef, type RefObject } from 'react';
import type { PlayerSummary, TradeSide, TradeSideKey } from '../../api/types';
import { Icon } from '../../components/Icon';
import { PositionChip, playerHref } from '../../components/PlayerLink';
import { PlayerSearch } from '../../components/PlayerSearch';
import { formatNumber } from '../../lib/format';
import { MAX_PLAYERS_PER_SIDE, type KnownPlayers } from './tradeQuery';

export const SIDE_TITLES: Readonly<Record<TradeSideKey, string>> = {
  give: 'You give',
  get: 'You get',
};

interface PlayerChipProps {
  id: number;
  player: PlayerSummary | undefined;
  value: number | undefined;
  sideTitle: string;
  onRemove: () => void;
}

function PlayerChip({ id, player, value, sideTitle, onRemove }: PlayerChipProps) {
  const name = player?.name ?? `Player #${id}`;
  return (
    <li className="trade-chip">
      <div className="trade-chip__main">
        <a className="trade-chip__name" href={playerHref(id)}>{name}</a>
        <span className="trade-chip__meta">
          <PositionChip position={player?.position ?? null} />
          {player?.team && <span className="muted">{player.team}</span>}
        </span>
      </div>
      <span className="trade-chip__value num" title="Rest-of-season trade value (model)">
        {value === undefined ? '…' : formatNumber(value, 0)}
      </span>
      <button type="button" className="icon-btn icon-btn--sm" aria-label={`Remove ${name} from ${sideTitle}`} onClick={onRemove}>
        <Icon name="close" size={16} />
      </button>
    </li>
  );
}

interface TradeSidePanelProps {
  side: TradeSideKey;
  ids: readonly number[];
  allIds: readonly number[];
  known: KnownPlayers;
  result: TradeSide | undefined;
  onAdd: (player: PlayerSummary) => void;
  onRemove: (id: number) => void;
  /** Lets the page move focus here (e.g. after Clear). */
  searchRef?: RefObject<HTMLInputElement | null>;
}

export function TradeSidePanel({ side, ids, allIds, known, result, onAdd, onRemove, searchRef }: TradeSidePanelProps) {
  const headingId = useId();
  const ownRef = useRef<HTMLInputElement>(null);
  const inputRef = searchRef ?? ownRef;
  const title = SIDE_TITLES[side];
  const isFull = ids.length >= MAX_PLAYERS_PER_SIDE;
  const handleRemove = (id: number) => {
    onRemove(id);
    inputRef.current?.focus();
  };
  const valueById = new Map(result?.players.map((entry) => [entry.player.id, entry.trade_value]) ?? []);
  return (
    <section className={`trade-side trade-side--${side}`} aria-labelledby={headingId}>
      <header className="trade-side__header">
        <h2 id={headingId} className="trade-side__title">{title}</h2>
        {result && (
          <span className="trade-side__total num" title="Package value after the consolidation discount">
            {formatNumber(result.adjusted_total, 0)}
            <span className="muted"> adj.</span>
          </span>
        )}
      </header>
      <PlayerSearch
        label={side === 'give' ? 'Add a player you give' : 'Add a player you get'}
        onSelect={onAdd}
        excludeIds={allIds}
        inputRef={inputRef}
      />
      {isFull && <p className="field__hint">Maximum of {MAX_PLAYERS_PER_SIDE} players per side.</p>}
      {ids.length === 0 ? (
        <p className="trade-side__empty">No players yet. Search above to add one.</p>
      ) : (
        <ul className="trade-chips" aria-label={`${title}: ${ids.length} player${ids.length === 1 ? '' : 's'}`}>
          {ids.map((id) => (
            <PlayerChip
              key={id}
              id={id}
              player={known[id]}
              value={valueById.get(id)}
              sideTitle={title}
              onRemove={() => handleRemove(id)}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
