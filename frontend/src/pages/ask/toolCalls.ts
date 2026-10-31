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

/** Plain-English names for the Sources list; unknown tools fall back to their raw name. */
export const TOOL_LABELS: Readonly<Record<string, string>> = {
  start_sit: 'Start/sit comparison',
  get_my_team: 'Your roster',
  get_my_matchup: 'Your matchup',
  get_weekly_rankings: 'This week’s projections',
  explain_projection: 'Projection breakdown',
  get_league_waivers: 'League waiver wire',
  get_waiver_targets: 'Waiver targets',
  get_trending_players: 'Trending adds',
  find_league_trades: 'Trade ideas in your league',
  find_trades: 'Trade ideas',
  evaluate_trade: 'Trade value',
  get_league_rosters: 'League rosters',
  get_power_rankings: 'Power rankings',
  get_playoff_outlook: 'Playoff odds',
  get_schedule_strength: 'Schedule strength',
  get_injury_report: 'Injury report',
  get_player_news: 'Player news',
  get_player_profile: 'Player profile',
  get_player_game_log: 'Game log',
  compare_players: 'Player comparison',
  get_fantasy_rankings: 'Rest-of-season rankings',
  get_expert_rankings: 'Expert rankings',
  get_buy_low_candidates: 'Buy-low candidates',
  get_sell_high_candidates: 'Sell-high candidates',
  get_leaderboard: 'Stat leaderboard',
  list_leaderboard_metrics: 'Stat list',
  search_players: 'Player search',
  remember: 'Saved a note',
};

export function toolLabel(name: string): string {
  return Object.hasOwn(TOOL_LABELS, name) ? TOOL_LABELS[name] : name;
}

export const EXAMPLE_PROMPTS: readonly string[] = [
  'Who are the best buy-low RBs right now?',
  "Is trading Bijan Robinson for Ja'Marr Chase fair?",
  'Which WRs are scoring well above their expected points?',
  'Show the top rushers by rush yards over expected this season.',
];

export const LLM_SETUP_MESSAGE =
  'The AI assistant isn’t configured yet. Add GROQ_API_KEY to backend/.env, then restart the backend.';

export const TOO_LARGE_MESSAGE =
  'That question needs more data than the free AI tier can take at once. Ask about fewer players or one league.';
