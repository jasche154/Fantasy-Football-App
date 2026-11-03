import { Fragment, useState } from 'react';
import type { OddsGame, PlayerProp, PropStatKey } from '../../api/types';
import { PlayerLink, PositionChip } from '../../components/PlayerLink';
import { formatNumber } from '../../lib/format';

const PROP_COLUMNS: readonly { key: PropStatKey; label: string; title: string; digits: number }[] = [
  { key: 'pass_yds', label: 'Pass yds', title: 'Passing yards', digits: 1 },
  { key: 'pass_tds', label: 'Pass TD', title: 'Passing touchdowns', digits: 2 },
  { key: 'ints', label: 'INT', title: 'Interceptions', digits: 2 },
  { key: 'rush_yds', label: 'Rush yds', title: 'Rushing yards', digits: 1 },
  { key: 'rec', label: 'Rec', title: 'Receptions', digits: 1 },
  { key: 'rec_yds', label: 'Rec yds', title: 'Receiving yards', digits: 1 },
  { key: 'scrimmage_tds', label: 'TD', title: 'Expected rushing + receiving touchdowns (from anytime-TD odds)', digits: 2 },
];

/** "CIN −3.5" from the home margin (positive = home favored); "Pick" for a zero spread. */
export function spreadLabel(game: Pick<OddsGame, 'home_team' | 'away_team' | 'spread_line'>): string {
  const spread = game.spread_line;
  if (spread === null) return '—';
  if (spread === 0) return 'Pick';
  return `${spread > 0 ? game.home_team : game.away_team} −${formatNumber(Math.abs(spread))}`;
}

export function kickoffLabel(game: Pick<OddsGame, 'kickoff' | 'gameday'>): string {
  if (!game.kickoff) return game.gameday ?? '—';
  return new Date(game.kickoff).toLocaleString(undefined, { weekday: 'short', hour: 'numeric', minute: '2-digit' });
}

/** "Props from 1.5 h before kickoff" (or when they were fetched, if the kickoff is unknown). */
export function propsTimingLabel(game: Pick<OddsGame, 'props_fetched_at' | 'props_hours_before_kickoff'>): string | null {
  if (!game.props_fetched_at) return null;
  const lead = game.props_hours_before_kickoff;
  if (lead !== null && lead >= 0) return `Props from ${formatNumber(lead)} h before kickoff`;
  return `Props fetched ${new Date(game.props_fetched_at).toLocaleString()}`;
}

/** Only the stat columns at least one player in the game has. */
export function visiblePropColumns(props: readonly PlayerProp[]) {
  return PROP_COLUMNS.filter((column) => props.some((prop) => prop.stats[column.key] !== undefined));
}

function PropsTable({ game }: { game: OddsGame }) {
  const columns = visiblePropColumns(game.props);
  return (
    <div className="odds-props">
      <p className="meta-line">{propsTimingLabel(game)} · expected stats implied by the betting lines</p>
      <div className="table-wrap">
        <table className="data-table data-table--compact">
          <caption className="sr-only">{`Player props, ${game.away_team} at ${game.home_team}`}</caption>
          <thead>
            <tr>
              <th scope="col" className="th--start">Player</th>
              {columns.map((column) => (
                <th key={column.key} scope="col" className="th--end" title={column.title}>{column.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {game.props.map((prop) => (
              <tr key={`${prop.player_id ?? prop.name}-${prop.team}`}>
                <td className="td--player">
                  {prop.player_id !== null ? (
                    <PlayerLink player={{ id: prop.player_id, name: prop.name, position: prop.position, team: prop.team }} />
                  ) : (
                    <>{prop.name} <PositionChip position={prop.position} /></>
                  )}
                </td>
                {columns.map((column) => (
                  <td key={column.key} className="num">{formatNumber(prop.stats[column.key], column.digits)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function GameRow({ game, isOpen, onToggle }: { game: OddsGame; isOpen: boolean; onToggle: () => void }) {
  const matchup = `${game.away_team} @ ${game.home_team}`;
  return (
    <tr>
      <td>{kickoffLabel(game)}</td>
      <th scope="row" className="th--start">{matchup}</th>
      <td className="num">{game.final ? `${game.away_score}–${game.home_score}` : '—'}</td>
      <td>{spreadLabel(game)}</td>
      <td className="num">{formatNumber(game.total_line)}</td>
      <td className="num">{formatNumber(game.away_implied)}</td>
      <td className="num">{formatNumber(game.home_implied)}</td>
      <td className="th--end">
        {game.props.length === 0 ? (
          <span className="muted">None yet</span>
        ) : (
          <button type="button" className="btn btn--ghost btn--sm" aria-expanded={isOpen} onClick={onToggle}>
            {isOpen ? 'Hide' : 'Show'} {game.props.length} props<span className="sr-only">{`, ${matchup}`}</span>
          </button>
        )}
      </td>
    </tr>
  );
}

export function OddsGamesTable({ games }: { games: readonly OddsGame[] }) {
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set());
  const toggle = (key: string) =>
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  return (
    <div className="table-wrap">
      <table className="data-table">
        <caption className="sr-only">Games with the closing spread, total and implied team totals</caption>
        <thead>
          <tr>
            <th scope="col" className="th--start">Kickoff</th>
            <th scope="col" className="th--start">Game</th>
            <th scope="col" className="th--end">Final</th>
            <th scope="col" className="th--start" title="Favorite and points they're favored by">Spread</th>
            <th scope="col" className="th--end" title="Over/under">Total</th>
            <th scope="col" className="th--end" title="Away team's implied points: (total − spread) / 2">Away implied</th>
            <th scope="col" className="th--end" title="Home team's implied points: (total + spread) / 2">Home implied</th>
            <th scope="col" className="th--end">Props</th>
          </tr>
        </thead>
        <tbody>
          {games.map((game) => {
            const key = `${game.away_team}@${game.home_team}`;
            const isOpen = open.has(key);
            return (
              <Fragment key={key}>
                <GameRow game={game} isOpen={isOpen} onToggle={() => toggle(key)} />
                {isOpen && (
                  <tr className="odds-props-row">
                    <td colSpan={8}><PropsTable game={game} /></td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
