import type { ChatResponse, ToolCallTrace } from '../../api/types';
import { readJsonStorage, writeJsonStorage, writeStorage } from '../../lib/storage';

export interface ChatEntry {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  toolCalls: readonly ToolCallTrace[];
  suggestions?: readonly string[];
  unverified?: readonly string[];
  dataAsOf?: ChatResponse['data_as_of'];
  stopReason?: ChatResponse['stop_reason'];
  provider?: string | null;
}

export interface StoredChat {
  entries: readonly ChatEntry[];
  memory: readonly string[];
  leagueId: number | null;
}

export const STORAGE_KEY = 'gridiron.askChat.v1';
export const MAX_STORED_ENTRIES = 40;
/** The server's message limits; stored text longer than this is cut, never dropped. */
export const MAX_TEXT: Readonly<Record<ChatEntry['role'], number>> = { user: 4000, assistant: 12000 };
export const EMPTY_CHAT: StoredChat = { entries: [], memory: [], leagueId: null };

type Json = Record<string, unknown>;

function isObject(value: unknown): value is Json {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function strings(value: unknown, maxLength: number): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string' && item.length > 0 && item.length <= maxLength)
    : [];
}

function toToolCall(value: unknown): ToolCallTrace | null {
  if (!isObject(value) || typeof value.name !== 'string' || typeof value.ok !== 'boolean') return null;
  return { name: value.name, ok: value.ok, arguments: isObject(value.arguments) ? value.arguments : {} };
}

function toDataAsOf(value: unknown): ChatEntry['dataAsOf'] {
  if (!isObject(value)) return undefined;
  const number = (item: unknown) => (typeof item === 'number' && Number.isFinite(item) ? item : null);
  return { season: number(value.season), week: number(value.week) };
}

/** Rebuilds one entry field by field; anything malformed is dropped rather than trusted. */
function toEntry(value: unknown, index: number): ChatEntry | null {
  if (!isObject(value) || (value.role !== 'user' && value.role !== 'assistant')) return null;
  if (typeof value.content !== 'string' || !value.content.trim()) return null;
  const limit = MAX_TEXT[value.role];
  const content = value.content.length > limit ? `${value.content.slice(0, limit - 1)}…` : value.content;
  const calls = Array.isArray(value.toolCalls) ? value.toolCalls.map(toToolCall) : [];
  return {
    id: `stored-${index}`,
    role: value.role,
    content,
    toolCalls: calls.filter((call): call is ToolCallTrace => call !== null),
    suggestions: strings(value.suggestions, 200),
    unverified: strings(value.unverified, 200),
    dataAsOf: toDataAsOf(value.dataAsOf),
    stopReason: value.stopReason === 'out_of_rounds' ? 'out_of_rounds' : undefined,
    provider: typeof value.provider === 'string' ? value.provider : null,
  };
}

/** The saved chat for this browser, validated on the way in (storage is editable by anything local). */
export function loadChat(): StoredChat {
  const raw = readJsonStorage(STORAGE_KEY);
  if (!isObject(raw)) return EMPTY_CHAT;
  const entries = Array.isArray(raw.entries)
    ? raw.entries.slice(-MAX_STORED_ENTRIES).map(toEntry).filter((entry): entry is ChatEntry => entry !== null)
    : [];
  const leagueId = typeof raw.leagueId === 'number' && Number.isInteger(raw.leagueId) ? raw.leagueId : null;
  return { entries, memory: strings(raw.memory, 200).slice(0, 10), leagueId };
}

export function saveChat(chat: StoredChat): void {
  if (chat.entries.length === 0 && chat.memory.length === 0 && chat.leagueId === null) {
    writeStorage(STORAGE_KEY, null);
    return;
  }
  writeJsonStorage(STORAGE_KEY, { ...chat, entries: chat.entries.slice(-MAX_STORED_ENTRIES) });
}
