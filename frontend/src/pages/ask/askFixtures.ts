import type { ChatResponse, MyLeague } from '../../api/types';

export function league(id: number, name: string, overrides: Partial<MyLeague> = {}): MyLeague {
  return { id, key: `449.l.${id}`, name, season: 2026, team: 'My Team', scoring: 'half', teams: 10, starters: null,
    week: 9, ...overrides };
}

export function chatReply(overrides: Partial<ChatResponse> = {}): ChatResponse {
  return { reply: 'Start Bijan Robinson.', tool_calls: [], memory: [], suggestions: [], unverified: [],
    stop_reason: 'answer', provider: 'groq', data_as_of: { season: 2026, week: 9 }, ...overrides };
}
