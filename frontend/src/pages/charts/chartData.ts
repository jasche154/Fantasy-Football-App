import { getLeaderboard, getPlayer } from '../../api/data';
import type { LeaderboardRow, LeagueSettings, Position } from '../../api/types';
import type { BarDatum } from '../../components/charts/BarChart';
import type { LineSeries } from '../../components/charts/LineChart';
import type { ScatterDatum } from '../../components/charts/ScatterChart';
import { parseMetricId, type MetricId, type WeeklyStatKey } from './autoChart';

export const LEADERS_SHOWN = 15;
const SCATTER_POOL = 200;

export async function loadTrend(
  playerIds: readonly number[],
  stat: WeeklyStatKey,
  settings: LeagueSettings,
  signal: AbortSignal,
): Promise<LineSeries[]> {
  const profiles = await Promise.all(playerIds.map((id) => getPlayer(id, settings, undefined, signal)));
  return profiles.map((profile) => ({
    name: profile.player.name,
    points: profile.game_log
      .filter((game) => game.season_type === null || game.season_type === 'REG')
      .flatMap((game) => {
        const value = game[stat];
        return typeof value === 'number' ? [{ x: game.week, y: value, label: game.opponent ? `vs ${game.opponent}` : undefined }] : [];
      }),
  }));
}

function leaderboard(id: MetricId, position: Position | null, limit: number, signal: AbortSignal) {
  return getLeaderboard({ ...parseMetricId(id), position: position ?? undefined, limit }, signal);
}

export async function loadLeaders(
  id: MetricId,
  position: Position | null,
  highlightIds: ReadonlySet<number>,
  signal: AbortSignal,
): Promise<BarDatum[]> {
  const board = await leaderboard(id, position, LEADERS_SHOWN, signal);
  return board.results.map((row) => ({
    label: row.player.name,
    sublabel: row.player.team ?? undefined,
    value: row.value,
    isHighlighted: highlightIds.has(row.player.id),
  }));
}

/** Players qualified for both metrics, joined by id. */
export function joinScatter(xs: readonly LeaderboardRow[], ys: readonly LeaderboardRow[],
  highlightIds: ReadonlySet<number>): ScatterDatum[] {
  const yById = new Map(ys.map((row) => [row.player.id, row.value]));
  return xs.flatMap((row) => {
    const y = yById.get(row.player.id);
    return y === undefined ? [] : [{ id: row.player.id, label: row.player.name, x: row.value, y,
      isHighlighted: highlightIds.has(row.player.id), player: row.player }];
  });
}

export async function loadScatter(
  xId: MetricId,
  yId: MetricId,
  position: Position | null,
  highlightIds: ReadonlySet<number>,
  signal: AbortSignal,
): Promise<ScatterDatum[]> {
  const [xs, ys] = await Promise.all([
    leaderboard(xId, position, SCATTER_POOL, signal),
    leaderboard(yId, position, SCATTER_POOL, signal),
  ]);
  return joinScatter(xs.results, ys.results, highlightIds);
}
