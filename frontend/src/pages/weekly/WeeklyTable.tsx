import { Fragment, useMemo, useState } from 'react';
import type { WeeklyMatchup, WeeklyPlayer, WeeklySourceKey, WeeklySourceValue } from '../../api/types';
import { FlagChips, StatusBadge } from '../../components/FlagChips';
import { PlayerLink } from '../../components/PlayerLink';
import { nextSort, SortableHeader, type SortState } from '../../components/SortableHeader';
import { EMPTY_VALUE, formatNumber } from '../../lib/format';
import { factorLabel, rankTone, TONE_LABELS } from '../../lib/matchup';
import { ExplainPanel } from './ExplainPanel';
import { DEFAULT_WEEKLY_SORT, sortWeekly, weeklyDefaultDirection, type WeeklySortKey } from './weeklySort';

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

/** Points scored, colored against the projection: beat it by 20%+ (green) or missed by 20%+ (red). */
function ActualCell({ row }: { row: WeeklyPlayer }) {
  const actual = row.actual ?? null;
  if (actual === null) {
    const sat = row.matchup.game_final;
    return <td className="num num--muted" title={sat ? 'Did not play' : 'Game not played yet'}>{sat ? 'DNP' : EMPTY_VALUE}</td>;
  }
  const ratio = row.projection > 0 ? actual / row.projection : 1;
  const tone = ratio >= 1.2 ? 'pos' : ratio <= 0.8 ? 'neg' : 'flat';
  const diff = actual - row.projection;
  return (
    <td className={`num actual actual--${tone}`} title={`${diff >= 0 ? '+' : ''}${formatNumber(diff)} vs projection`}>
      {formatNumber(actual)}
    </td>
  );
}

function WeeklyRow({ row, week }: { row: WeeklyPlayer; week: number }) {
  const position = row.player.position;
  const [showWhy, setShowWhy] = useState(false);
  return (
    <Fragment>
    <tr>
      <td className="num td--rank">{row.rank}</td>
      <td className="td--player">
        <PlayerLink player={row.player} />
        <StatusBadge status={row.status} playProb={row.play_prob} />
        <FlagChips flags={row.flags} />
        <button type="button" className="why-btn" aria-expanded={showWhy}
          aria-label={`Why ${row.player.name} projects ${formatNumber(row.projection)}`} onClick={() => setShowWhy((v) => !v)}>
          Why?
        </button>
      </td>
      <td className="td--pos">{row.pos_rank}</td>
      <td>
        <MatchupCell matchup={row.matchup} position={position} />
      </td>
      <td className="num">{formatNumber(row.matchup.implied_total)}</td>
      <td className="num">
        <span className="value-pill">{formatNumber(row.projection)}</span>
        {row.floor !== null && row.ceiling !== null && (
          <span className="source-rank" title="10th to 90th percentile outcome">{formatNumber(row.floor)}–{formatNumber(row.ceiling)}</span>
        )}
      </td>
      <ActualCell row={row} />
      {SOURCE_COLUMNS.map((column) => (
        <SourceCell key={column.key} value={row.sources[column.key]} position={position} />
      ))}
    </tr>
    {showWhy && (
      <tr className="explain-row">
        <td colSpan={7 + SOURCE_COLUMNS.length}><ExplainPanel playerId={row.player.id} week={week} /></td>
      </tr>
    )}
    </Fragment>
  );
}

interface WeeklyTableProps {
  rows: readonly WeeklyPlayer[];
  caption: string;
  week: number;
}

export function WeeklyTable({ rows, caption, week }: WeeklyTableProps) {
  const [sort, setSort] = useState<SortState<WeeklySortKey>>(DEFAULT_WEEKLY_SORT);
  const sorted = useMemo(() => sortWeekly(rows, sort), [rows, sort]);
  const onSort = (key: WeeklySortKey) => setSort((current) => nextSort(current, key, weeklyDefaultDirection));
  return (
    <div className="table-wrap table-wrap--tall">
      <table className="data-table weekly-table">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            <SortableHeader columnKey="rank" sort={sort} onSort={onSort} title="Rank by blended projection">Rank</SortableHeader>
            <th scope="col">Player</th>
            <th scope="col" title="Positional rank">Pos</th>
            <th scope="col" title="Opponent and opponent-adjusted matchup grade">Matchup</th>
            <SortableHeader columnKey="implied" sort={sort} onSort={onSort} title="Vegas implied team points (spread + total)">
              Team total
            </SortableHeader>
            <SortableHeader columnKey="proj" sort={sort} onSort={onSort}
              title="Expected points (if active x chance to play), with the 10th-90th percentile range">
              Proj
            </SortableHeader>
            <SortableHeader columnKey="actual" sort={sort} onSort={onSort}
              title="Points scored this week in your scoring, once the game is final (green: beat the projection by 20%+, red: missed by 20%+)">
              Actual
            </SortableHeader>
            {SOURCE_COLUMNS.map((column) => (
              <SortableHeader key={column.key} columnKey={column.key} sort={sort} onSort={onSort} title={column.title}>
                <span className={`source-dot source--${column.key}`} aria-hidden="true" />
                {column.label}
              </SortableHeader>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <WeeklyRow key={row.player.id} row={row} week={week} />
          ))}
        </tbody>
      </table>
    </div>
  );
}
