import { settingsToParams } from '../lib/query';
import { apiRequest } from './client';
import type {
  ExpertRankingsResponse,
  InsightsResponse,
  LeaderboardResponse,
  LeagueSettings,
  MetricCategory,
  Meta,
  PlayerProfile,
  PlayerSummary,
  Position,
  RankingsResponse,
  RushingLeadersResponse,
  ScheduleStrengthResponse,
  TrendingRow,
  WeeklyRankingsResponse,
} from './types';

export function getHealth(signal?: AbortSignal): Promise<{ status: string }> {
  return apiRequest('/api/health', { signal });
}

export function getMeta(signal?: AbortSignal): Promise<Meta> {
  return apiRequest('/api/meta', { signal });
}

export interface SearchOptions {
  position?: Position;
  limit?: number;
}

export function searchPlayers(
  q: string,
  options: SearchOptions = {},
  signal?: AbortSignal,
): Promise<{ results: PlayerSummary[] }> {
  return apiRequest('/api/players/search', {
    query: { q, position: options.position, limit: options.limit },
    signal,
  });
}

export function getPlayer(
  id: number,
  settings: LeagueSettings,
  season?: number,
  signal?: AbortSignal,
): Promise<PlayerProfile> {
  return apiRequest(`/api/players/${encodeURIComponent(String(id))}`, {
    query: { season, ...settingsToParams(settings) },
    signal,
  });
}

export interface ListOptions {
  position?: Position;
  limit?: number;
}

export function getRankings(
  settings: LeagueSettings,
  options: ListOptions = {},
  signal?: AbortSignal,
): Promise<RankingsResponse> {
  return apiRequest('/api/rankings', {
    query: { position: options.position, limit: options.limit, ...settingsToParams(settings) },
    signal,
  });
}

export function getLeaderboardMetrics(
  signal?: AbortSignal,
): Promise<{ categories: MetricCategory[] }> {
  return apiRequest('/api/leaderboards/metrics', { signal });
}

export interface LeaderboardParams {
  category: string;
  metric: string;
  season?: number;
  min_volume?: number;
  limit?: number;
  ascending?: boolean;
  position?: Position;
}

export function getLeaderboard(
  params: LeaderboardParams,
  signal?: AbortSignal,
): Promise<LeaderboardResponse> {
  return apiRequest('/api/leaderboards', { query: { ...params }, signal });
}

export interface RushingLeadersParams {
  metric?: string;
  season?: number;
  limit?: number;
  min_attempts?: number;
}

/** Legacy endpoint kept for backwards compatibility; the UI uses `getLeaderboard`. */
export function getRushingLeaders(
  params: RushingLeadersParams = {},
  signal?: AbortSignal,
): Promise<RushingLeadersResponse> {
  return apiRequest('/api/rankings/rushing', { query: { ...params }, signal });
}

export type InsightKind = 'buy-low' | 'sell-high';

export function getInsights(
  kind: InsightKind,
  settings: LeagueSettings,
  options: ListOptions = {},
  signal?: AbortSignal,
): Promise<InsightsResponse> {
  return apiRequest(`/api/insights/${kind}`, {
    query: { position: options.position, limit: options.limit, ...settingsToParams(settings) },
    signal,
  });
}

export function getBuyLow(
  settings: LeagueSettings,
  options: ListOptions = {},
  signal?: AbortSignal,
): Promise<InsightsResponse> {
  return getInsights('buy-low', settings, options, signal);
}

export function getSellHigh(
  settings: LeagueSettings,
  options: ListOptions = {},
  signal?: AbortSignal,
): Promise<InsightsResponse> {
  return getInsights('sell-high', settings, options, signal);
}

export function getTrending(
  type: 'add' | 'drop' = 'add',
  limit?: number,
  signal?: AbortSignal,
): Promise<{ results: TrendingRow[] }> {
  return apiRequest('/api/insights/trending', { query: { type, limit }, signal });
}

export function getExpertRankings(
  options: ListOptions = {},
  signal?: AbortSignal,
): Promise<ExpertRankingsResponse> {
  return apiRequest('/api/experts/rankings', {
    query: { position: options.position, limit: options.limit },
    signal,
  });
}

export interface WeeklyOptions extends ListOptions {
  week?: number;
}

export function getWeeklyRankings(
  settings: LeagueSettings,
  options: WeeklyOptions = {},
  signal?: AbortSignal,
): Promise<WeeklyRankingsResponse> {
  return apiRequest('/api/rankings/weekly', {
    query: { week: options.week, position: options.position, limit: options.limit, ...settingsToParams(settings) },
    signal,
  });
}

export function getScheduleStrength(
  settings: LeagueSettings,
  position: Position,
  week?: number,
  signal?: AbortSignal,
): Promise<ScheduleStrengthResponse> {
  return apiRequest('/api/schedule-strength', { query: { position, week, ...settingsToParams(settings) }, signal });
}
