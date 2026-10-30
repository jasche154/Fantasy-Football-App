import { useState } from 'react';
import { getComparison } from '../../api/data';
import type { CompareGame, CompareResponse, PlayerSummary } from '../../api/types';
import { AsyncView } from '../../components/AsyncView';
import { Card, PageHeader } from '../../components/Card';
import { LineChart, type LineSeries } from '../../components/charts/LineChart';
import { PlayerLink } from '../../components/PlayerLink';
import { PlayerSearch } from '../../components/PlayerSearch';
import { SelectField } from '../../components/SelectField';
import { EmptyState, Skeleton } from '../../components/StatusViews';
import { useAsync } from '../../hooks/useAsync';
import { useDocumentTitle } from '../../hooks/useDocumentTitle';
import { EMPTY_VALUE, formatNumber } from '../../lib/format';
import { parsePositiveInt } from '../../router/routes';
import { useMeta } from '../../state/MetaContext';
import { useLeagueSettings } from '../../state/SettingsContext';

export const MAX_PLAYERS = 4;
const MAX_SEASONS = 4;
const ROLLING_GAMES = 3;

type Stat = 'fantasy_points' | 'expected_points' | 'targets' | 'carries' | 'scrimmage_yards' | 'touchdowns' | 'snap_pct';
const STATS: readonly { value: Stat; label: string }[] = [
  { value: 'fantasy_points', label: 'Fantasy points' },
  { value: 'expected_points', label: 'Expected points' },
  { value: 'targets', label: 'Targets' },
  { value: 'carries', label: 'Carries' },
  { value: 'scrimmage_yards', label: 'Scrimmage yards' },
  { value: 'touchdowns', label: 'Touchdowns' },
  { value: 'snap_pct', label: 'Snap %' },
];

function idsFrom(text: string | undefined): number[] {
  return (text ?? '').split(',').map((part) => parsePositiveInt(part.trim())).filter((id): id is number => id !== null)
    .slice(0, MAX_PLAYERS);
}

/** Every (season, week) any player played, in order, so seasons sit side by side on one axis. */
export function timeline(data: CompareResponse): string[] {
  const keys = new Set(data.players.flatMap((p) => p.games.map((g) => `${g.season}-${String(g.week).padStart(2, '0')}`)));
  return [...keys].sort();
}

function rolling(values: readonly (number | null)[], window: number): (number | null)[] {
  return values.map((_, index) => {
    const recent = values.slice(Math.max(0, index - window + 1), index + 1).filter((v): v is number => v !== null);
    return recent.length > 0 ? recent.reduce((a, b) => a + b, 0) / recent.length : null;
  });
}

export function buildSeries(data: CompareResponse, stat: Stat, smooth: boolean): { series: LineSeries[]; axis: string[] } {
  const axis = timeline(data);
  const index = new Map(axis.map((key, i) => [key, i]));
  const series = data.players.map((entry) => {
    const games = [...entry.games].sort((a, b) => a.season - b.season || a.week - b.week);
    const raw = games.map((g: CompareGame) => (typeof g[stat] === 'number' ? (g[stat] as number) : null));
    const values = smooth ? rolling(raw, ROLLING_GAMES) : raw;
    return {
      name: entry.player.name,
      points: games.flatMap((g, i) => {
        const value = values[i];
        const x = index.get(`${g.season}-${String(g.week).padStart(2, '0')}`);
        return value === null || x === undefined ? [] : [{ x, y: value, label: g.opponent ? `vs ${g.opponent}` : undefined }];
      }),
    };
  });
  return { series, axis };
}

function axisLabel(key: string | undefined): string {
  if (!key) return '';
  const [season, week] = key.split('-');
  return `'${season.slice(2)} W${Number(week)}`;
}

function CompareChart({ data, stat, smooth }: { data: CompareResponse; stat: Stat; smooth: boolean }) {
  const { series, axis } = buildSeries(data, stat, smooth);
  const label = STATS.find((s) => s.value === stat)?.label ?? stat;
  if (series.every((s) => s.points.length === 0)) return <EmptyState title={`No ${label.toLowerCase()} for these seasons.`} />;
  return (
    <LineChart title={`${label} by game`} description={`${label} for ${series.map((s) => s.name).join(', ')}`}
      series={series} xLabel="Game" yLabel={smooth ? `${label} (${ROLLING_GAMES}-game avg)` : label}
      xFormat={(x) => axisLabel(axis[x])} xTickEvery={Math.max(1, Math.ceil(axis.length / 12))} />
  );
}

function SummaryTable({ data }: { data: CompareResponse }) {
  return (
    <div className="table-wrap">
      <table className="data-table">
        <caption className="sr-only">Season summaries</caption>
        <thead>
          <tr>
            <th scope="col">Player</th><th scope="col">Season</th><th scope="col" className="num">Games</th>
            <th scope="col" className="num">PPG</th><th scope="col" className="num">Expected PPG</th>
            <th scope="col" className="num">Best</th><th scope="col" className="num">Worst</th>
            <th scope="col" className="num">Targets/g</th><th scope="col" className="num">Carries/g</th>
          </tr>
        </thead>
        <tbody>
          {data.players.flatMap((entry) => entry.summary.map((s) => (
            <tr key={`${entry.player.id}-${s.season}`}>
              <td className="td--player"><PlayerLink player={entry.player} /></td>
              <td>{s.season}</td><td className="num">{s.games}</td>
              <td className="num"><strong>{formatNumber(s.ppg)}</strong></td>
              <td className="num">{s.expected_ppg === null ? EMPTY_VALUE : formatNumber(s.expected_ppg)}</td>
              <td className="num">{formatNumber(s.best)}</td><td className="num">{formatNumber(s.worst)}</td>
              <td className="num">{formatNumber(s.targets_per_game)}</td><td className="num">{formatNumber(s.carries_per_game)}</td>
            </tr>
          )))}
        </tbody>
      </table>
    </div>
  );
}

/** Compare any 2-4 players game by game, across up to four seasons. */
export function ComparePage({ query }: { query: Readonly<Record<string, string>> }) {
  useDocumentTitle('Compare Players');
  const meta = useMeta();
  const { settings } = useLeagueSettings();
  const [ids, setIds] = useState<number[]>(() => idsFrom(query.ids));
  const [picked, setPicked] = useState<PlayerSummary[]>([]);
  const allSeasons = (meta.data?.seasons ?? []).slice().sort((a, b) => b - a).slice(0, MAX_SEASONS);
  const [seasons, setSeasons] = useState<number[]>(() => idsFrom(query.seasons));
  const [stat, setStat] = useState<Stat>('fantasy_points');
  const [smooth, setSmooth] = useState(false);
  const chosenSeasons = seasons.length > 0 ? seasons : allSeasons.slice(0, 1);
  const data = useAsync((signal) => (ids.length > 0 && chosenSeasons.length > 0
    ? getComparison(ids, chosenSeasons, settings, signal) : Promise.resolve(null)),
  [ids.join(','), chosenSeasons.join(','), settings]);

  const add = (player: PlayerSummary) => {
    if (ids.includes(player.id) || ids.length >= MAX_PLAYERS) return;
    setIds([...ids, player.id]);
    setPicked([...picked, player]);
  };
  const remove = (id: number) => setIds(ids.filter((x) => x !== id));
  const toggleSeason = (season: number) => {
    const next = chosenSeasons.includes(season) ? chosenSeasons.filter((s) => s !== season) : [...chosenSeasons, season];
    setSeasons(next.length > 0 ? next : chosenSeasons);
  };
  const nameOf = (id: number) => data.data?.players.find((p) => p.player.id === id)?.player.name
    ?? picked.find((p) => p.id === id)?.name ?? `Player ${id}`;

  return (
    <>
      <PageHeader title="Compare Players" subtitle="Any players, game by game, across seasons." />
      <Card title="Players and seasons">
        <div className="compare-controls">
          {ids.length < MAX_PLAYERS && <PlayerSearch label="Add a player" onSelect={add} excludeIds={ids} />}
          <ul className="chip-list" aria-label="Players being compared">
            {ids.map((id) => (
              <li key={id} className="chip">
                {nameOf(id)}{' '}
                <button type="button" className="chip__remove" aria-label={`Remove ${nameOf(id)}`} onClick={() => remove(id)}>×</button>
              </li>
            ))}
          </ul>
          <fieldset className="compare-seasons">
            <legend>Seasons</legend>
            {allSeasons.map((season) => (
              <label key={season}>
                <input type="checkbox" checked={chosenSeasons.includes(season)} onChange={() => toggleSeason(season)} /> {season}
              </label>
            ))}
          </fieldset>
          <SelectField label="Stat" isInline value={stat} options={STATS.map((s) => ({ value: s.value, label: s.label }))}
            onChange={(value) => setStat(value as Stat)} />
          <label className="compare-toggle">
            <input type="checkbox" checked={smooth} onChange={(event) => setSmooth(event.target.checked)} /> {ROLLING_GAMES}-game average
          </label>
        </div>
      </Card>
      {ids.length === 0 ? (
        <EmptyState title="Add players to compare.">Search above; up to {MAX_PLAYERS} players.</EmptyState>
      ) : (
        <AsyncView state={data} loading={<Skeleton lines={6} label="Loading game logs…" />}
          isEmpty={(d) => !d || d.players.length === 0} empty={<EmptyState title="No games for these players and seasons." />}>
          {(d) => d && (
            <>
              <Card title="Game by game"><CompareChart data={d} stat={stat} smooth={smooth} /></Card>
              <Card title="By season"><SummaryTable data={d} /></Card>
            </>
          )}
        </AsyncView>
      )}
    </>
  );
}
