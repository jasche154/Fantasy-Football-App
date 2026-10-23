import { apiRequest } from './client';
import type {
  ChatMessage,
  ChatResponse,
  IntegrationsStatus,
  ToolDef,
  TradeRequest,
  TradeResult,
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
