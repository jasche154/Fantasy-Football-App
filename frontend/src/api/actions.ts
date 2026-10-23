import { apiRequest } from './client';
import type {
  ChatMessage,
  ChatResponse,
  IntegrationsStatus,
  ToolDef,
  TradeRequest,
  TradeOffer,
  TradeResult,
  WeeklyRefreshResponse,
  YahooImportResult,
  YahooLeague,
} from './types';

export function evaluateTrade(request: TradeRequest, signal?: AbortSignal): Promise<TradeResult> {
  return apiRequest('/api/trade/evaluate', { method: 'POST', body: request, signal });
}

export function getAgentTools(signal?: AbortSignal): Promise<{ tools: ToolDef[] }> {
  return apiRequest('/api/agent/tools', { signal });
}

export function callAgentTool(
  name: string,
  args: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<{ result: unknown }> {
  return apiRequest(`/api/agent/tools/${encodeURIComponent(name)}`, {
    method: 'POST',
    body: { arguments: args },
    signal,
  });
}

export function sendChat(messages: ChatMessage[], signal?: AbortSignal): Promise<ChatResponse> {
  return apiRequest('/api/agent/chat', { method: 'POST', body: { messages }, signal });
}

export function getIntegrationsStatus(signal?: AbortSignal): Promise<IntegrationsStatus> {
  return apiRequest('/api/integrations/status', { signal });
}

export function getYahooAuthUrl(signal?: AbortSignal): Promise<{ url: string }> {
  return apiRequest('/api/integrations/yahoo/auth-url', { signal });
}

/** `code` is the full redirected URL (preferred: it carries the `state` check) or a bare code. */
export function exchangeYahooCode(code: string, signal?: AbortSignal): Promise<{ connected: true }> {
  return apiRequest('/api/integrations/yahoo/exchange', {
    method: 'POST',
    body: { code },
    signal,
  });
}

export function getYahooLeagues(signal?: AbortSignal): Promise<{ leagues: YahooLeague[] }> {
  return apiRequest('/api/integrations/yahoo/leagues', { signal });
}

export function importYahooLeague(
  leagueKey: string,
  signal?: AbortSignal,
): Promise<YahooImportResult> {
  return apiRequest(
    `/api/integrations/yahoo/leagues/${encodeURIComponent(leagueKey)}/import`,
    { method: 'POST', signal },
  );
}

/** Re-fetch Yahoo and ESPN for a week (force skips their caches). Props keep their credit budget. */
export function refreshWeeklySources(week: number, force = true): Promise<WeeklyRefreshResponse> {
  return apiRequest('/api/rankings/weekly/refresh', { method: 'POST', query: { week, force } });
}

export function findTrades(
  myPlayers: readonly number[],
  settings: TradeRequest['settings'],
  wantPosition?: string,
  signal?: AbortSignal,
): Promise<{ weeks_remaining: number; results: TradeOffer[] }> {
  return apiRequest('/api/trade/find', {
    method: 'POST',
    body: { my_players: [...myPlayers], want_position: wantPosition || undefined, settings },
    signal,
  });
}
