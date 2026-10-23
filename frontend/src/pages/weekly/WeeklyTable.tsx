import type { WeeklyMatchup, WeeklyPlayer, WeeklySourceKey, WeeklySourceValue } from '../../api/types';
import { PlayerLink } from '../../components/PlayerLink';
import { EMPTY_VALUE, formatNumber } from '../../lib/format';
import { factorLabel, rankTone, TONE_LABELS } from '../../lib/matchup';

interface SourceColumn {
  key: WeeklySourceKey;
  label: string;
  title: string;
}

export const SOURCE_COLUMNS: readonly SourceColumn[] = [
  { key: 'vegas_props', label: 'Props', title: 'Points implied by Vegas player props' },
  { key: 'josh_hayden', label: 'Josh & Hayden', title: 'Yahoo’s Josh Norris and Hayden Winks: their average rank, as points' },
  { key: 'yahoo_consensus', label: 'Yahoo', title: 'Yahoo’s 40+ expert consensus rank, as points' },
  { key: 'espn', label: 'ESPN', title: 'ESPN projection' },
  { key: 'model', label: 'Model', title: 'This app’s model: rest-of-season rate × opponent-adjusted matchup × Vegas implied total' },
];

function MatchupCell({ matchup, position }: { matchup: WeeklyMatchup; position: string | null }) {
  const tone = rankTone(matchup.sos_rank);
  const detail =
    matchup.sos_rank === null
      ? 'No defensive data yet'
      : `${matchup.opponent} is #${matchup.sos_rank} easiest vs ${position ?? 'this position'}: ${factorLabel(matchup.sos_factor)} vs what offenses usually score`;
  return (
    <div className="matchup">
      <span className="matchup__opp">
        {matchup.home ? 'vs' : '@'} {matchup.opponent}
      </span>
      <span className={`matchup-grade matchup-grade--${tone}`} title={detail}>
        {TONE_LABELS[tone]}
        {matchup.sos_rank !== null && <span className="matchup-grade__rank">#{matchup.sos_rank}</span>}
      </span>
      {matchup.game_final && <span className="status-badge">Final</span>}
      <span className="sr-only">{detail}</span>
    </div>
  );
}

function SourceCell({ value, position }: { value: WeeklySourceValue | undefined; position: string | null }) {
  if (!value) return <td className="num num--muted">{EMPTY_VALUE}</td>;
  return (
    <td className="num">
      {formatNumber(value.points)}
      {value.rank !== null && (
        <span className="source-rank">
          {position}
          {formatNumber(value.rank, Number.isInteger(value.rank) ? 0 : 1)}
        </span>
      )}
    </td>
  );
}

function WeeklyRow({ row }: { row: WeeklyPlayer }) {
  const position = row.player.position;
  return (
    <tr>
      <td className="num td--rank">{row.rank}</td>
      <td className="td--player">
        <PlayerLink player={row.player} />
      </td>
      <td className="td--pos">{row.pos_rank}</td>
      <td>
        <MatchupCell matchup={row.matchup} position={position} />
      </td>
      <td className="num">{formatNumber(row.matchup.implied_total)}</td>
      <td className="num">
        <span className="value-pill">{formatNumber(row.projection)}</span>
      </td>
      {SOURCE_COLUMNS.map((column) => (
        <SourceCell key={column.key} value={row.sources[column.key]} position={position} />
      ))}
    </tr>
  );
}

interface WeeklyTableProps {
  rows: readonly WeeklyPlayer[];
  caption: string;
}

export function WeeklyTable({ rows, caption }: WeeklyTableProps) {
  return (
    <div className="table-wrap table-wrap--tall">
      <table className="data-table weekly-table">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            <th scope="col" className="num" title="Rank by blended projection">Rank</th>
            <th scope="col">Player</th>
            <th scope="col" title="Positional rank">Pos</th>
            <th scope="col" title="Opponent and opponent-adjusted matchup grade">Matchup</th>
            <th scope="col" className="num" title="Vegas implied team points (spread + total)">Team total</th>
            <th scope="col" className="num th--accent" title="Blended projected fantasy points">Proj</th>
            {SOURCE_COLUMNS.map((column) => (
              <th key={column.key} scope="col" className="num" title={column.title}>
                <span className={`source-dot source--${column.key}`} aria-hidden="true" />
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <WeeklyRow key={row.player.id} row={row} />
          ))}
        </tbody>
      </table>
    </div>
  );
}
