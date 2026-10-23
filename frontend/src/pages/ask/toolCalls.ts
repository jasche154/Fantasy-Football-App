const MAX_SUMMARY_LENGTH = 80;

function formatArgument(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value) && value.every((item) => typeof item !== 'object' || item === null)) {
    return value.map((item) => String(item)).join(', ');
  }
  try {
    return JSON.stringify(value);
  } catch {
    return '[unserialisable]';
  }
}

/** `{position: "RB", limit: 5}` -> "position: RB, limit: 5" (truncated for chips). */
export function summarizeArguments(args: Readonly<Record<string, unknown>>): string {
  const text = Object.entries(args)
    .map(([key, value]) => `${key}: ${formatArgument(value)}`)
    .join(', ');
  return text.length > MAX_SUMMARY_LENGTH ? `${text.slice(0, MAX_SUMMARY_LENGTH - 1)}…` : text;
}

export const EXAMPLE_PROMPTS: readonly string[] = [
  'Who are the best buy-low RBs right now?',
  "Is trading Bijan Robinson for Ja'Marr Chase fair?",
  'Which WRs are scoring well above their expected points?',
  'Show the top rushers by rush yards over expected this season.',
];

export const LLM_SETUP_MESSAGE =
  'The AI assistant isn’t configured yet. Add GROQ_API_KEY to backend/.env, then restart the backend.';
