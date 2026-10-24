import type {
  GameLogRow,
  Insight,
  IntegrationsStatus,
  Meta,
  PlayerProfile,
  PlayerSummary,
  RankedPlayer,
  TradeResult,
  TradeSidePlayer,
} from '../api/types';

export function player(id: number, name: string, position = 'RB', team: string | null = 'ATL'): PlayerSummary {
  return { id, name, position, team };
}

export const BIJAN = player(1, 'Bijan Robinson', 'RB', 'ATL');
export const CHASE = player(2, "Ja'Marr Chase", 'WR', 'CIN');
export const KELCE = player(3, 'Travis Kelce', 'TE', 'KC');
export const ALLEN = player(4, 'Josh Allen', 'QB', 'BUF');

export function integrations(overrides: Partial<IntegrationsStatus> = {}): IntegrationsStatus {
  return { fantasypros: false, odds_api: false, yahoo_configured: true, yahoo_connected: false, llm: true, ...overrides };
}

export function meta(overrides: Partial<Meta> = {}): Meta {
  return {
    current_season: 2025,
    latest_week: 7,
    current_week: 8,
    seasons: [2023, 2024, 2025],
    positions: ['QB', 'RB', 'WR', 'TE'],
    scoring_presets: ['ppr', 'half', 'std'],
    integrations: integrations(),
    ...overrides,
  };
}

export function ranked(rank: number, who: PlayerSummary, overrides: Partial<RankedPlayer> = {}): RankedPlayer {
  return {
    rank,
    pos_rank: `${who.position}${rank}`,
    player: who,
    games: 7,
    ppg: 20 - rank,
    expected_ppg: 18 - rank,
    ros_ppg: 19 - rank,
    vorp_per_game: 8 - rank,
    trade_value: 100 - rank * 10,
    ecr: rank,
    schedule_factor: 1,
    playoff_factor: 1,
    ...overrides,
  };
}

export function insight(who: PlayerSummary, overrides: Partial<Insight> = {}): Insight {
  return {
    player: who,
    score: 3.2,
    games: 6,
    ppg: 11.2,
    expected_ppg: 15.1,
    fpoe_per_game: -3.9,
    td_diff: -2.1,
    recent_ppg: 9.8,
    usage_trend: 1.2,
    trade_value: 84,
    reasons: [
      `Scoring 3.9 fewer PPR pts/game than his usage predicts (11.2 vs 15.1).`,
      'Touchdowns are 2.1 below expectation.',
    ],
    ...overrides,
  };
}

function sidePlayer(who: PlayerSummary, value: number, weight: number): TradeSidePlayer {
  return { player: who, trade_value: value, ros_ppg: 15, vorp_per_game: 5, weight, adjusted_value: value * weight };
}

export function tradeResult(overrides: Partial<TradeResult> = {}): TradeResult {
  return {
    give: { players: [sidePlayer(BIJAN, 120, 1)], raw_total: 120, adjusted_total: 120 },
    get: { players: [sidePlayer(CHASE, 108, 1)], raw_total: 108, adjusted_total: 108 },
    difference: -12,
    percent_difference: -0.1,
    verdict: 'slight_loss',
    summary: 'You give up about 10% more rest-of-season value than you get back.',
    balance_suggestions: [
      { player: KELCE, trade_value: 14, add_to: 'get', resulting_percent_difference: 0.01 },
    ],
    weeks_remaining: 10,
    ...overrides,
  };
}

export function gameLogRow(week: number, overrides: Partial<GameLogRow> = {}): GameLogRow {
  return {
    week,
    season_type: 'REG',
    opponent: 'DAL',
    fantasy_points: 10 + week,
    expected_points: 12,
    passing_yards: 0,
    passing_tds: 0,
    interceptions: 0,
    carries: 15,
    rushing_yards: 80,
    rushing_tds: 1,
    targets: 4,
    receptions: 3,
    receiving_yards: 25,
    receiving_tds: 0,
    snap_pct: 72,
    ...overrides,
  };
}

export function profile(overrides: Partial<PlayerProfile> = {}): PlayerProfile {
  return {
    player: { ...BIJAN, gsis_id: '00-0039000' },
    season: 2025,
    scoring: 'ppr',
    summary: {
      games: 3,
      fantasy_points: 39,
      ppg: 13,
      expected_ppg: 12,
      fpoe_per_game: 1,
      targets: 12,
      receptions: 9,
      carries: 45,
      passing_yards: 0,
      rushing_yards: 240,
      receiving_yards: 75,
      total_tds: 3,
      target_share: 0.125,
      snap_pct: 72.5,
    },
    projection: { ros_ppg: 17.5, vorp_per_game: 6.2, trade_value: 120, pos_rank: 'RB1' },
    advanced: {
      rushing: { rush_yards_over_expected_per_att: 1.05, efficiency: 3.4, avg_time_to_los: null },
      receiving: null,
      passing: null,
    },
    game_log: [gameLogRow(1), gameLogRow(2, { expected_points: null }), gameLogRow(3)],
    signals: { buy_low: true, sell_high: false, reasons: ['Usage is rising: +2.1 expected pts/game lately.'] },
    ...overrides,
  };
}
