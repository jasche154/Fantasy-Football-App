/**
 * Replay League test data: a 12-team half-PPR league in the 2023 season, week 5, phase "open". The user is
 * team 4 ("Your Team", 3-1, 2nd) and plays team 9 this week. Every builder takes overrides for the fields
 * a test cares about.
 */
import { useState } from 'react';
import type {
  ReplayActionResult,
  ReplayEvent,
  ReplayEventKind,
  ReplayGameListing,
  ReplayInjury,
  ReplayInjuryTag,
  ReplayPlayerDetail,
  ReplayPlayerRef,
  ReplayPlayerRow,
  ReplayPosition,
  ReplayPractice,
  ReplayReadiness,
  ReplayRecord,
  ReplayRosterRow,
  ReplaySeasons,
  ReplaySide,
  ReplaySlot,
  ReplayStatLine,
  ReplaySummary,
  ReplayTeam,
  ReplayTeamRef,
  ReplayUsage,
  ReplayWireRow,
} from '../../api/types';
import type { ReplayViewProps } from './shared';

export const GAME_ID = 7;
export const USER_TEAM_ID = 4;
export const OPPONENT_TEAM_ID = 9;

const TEAM_NAMES = ['Gridiron Ghosts', 'Blitz Brigade', 'End Zone Elite', 'Your Team', 'Hail Marys', 'Pigskin Pros',
  'Fourth and Long', 'Red Zone Rebels', 'Sunday Scaries', 'Goal Line Gang', 'Two Minute Drill', 'Waiver Wizards'];

/** The league's 12 teams, team_id 1..12; team 4 is the user's. */
export const TEAMS: ReplayTeamRef[] = TEAM_NAMES.map((name, index) => ({ team_id: index + 1, name, is_user: index + 1 === USER_TEAM_ID,
  tendency: null }));

export function teamRef(teamId: number): ReplayTeamRef {
  return TEAMS[teamId - 1];
}

export function playerRef(id: number, name: string, position: ReplayPosition, team: string | null,
  injury: ReplayInjury | null = null): ReplayPlayerRef {
  return { id, name, position, team, injury };
}

/** A tagged copy of a player (the shared constants stay untagged): `tagged(OLAVE, 'IR', null, 'Knee')`. */
export function tagged(player: ReplayPlayerRef, tag: ReplayInjuryTag, practice: ReplayPractice | null = null,
  injury: string | null = null): ReplayPlayerRef {
  return { ...player, injury: { tag, practice, injury } };
}

export const MAHOMES = playerRef(101, 'Patrick Mahomes', 'QB', 'KC');
export const MCCAFFREY = playerRef(102, 'Christian McCaffrey', 'RB', 'SF');
export const ETIENNE = playerRef(103, 'Travis Etienne', 'RB', 'JAX');
export const HILL = playerRef(104, 'Tyreek Hill', 'WR', 'MIA');
export const NACUA = playerRef(105, 'Puka Nacua', 'WR', 'LA');
export const KELCE = playerRef(106, 'Travis Kelce', 'TE', 'KC');
export const PACHECO = playerRef(107, 'Isiah Pacheco', 'RB', 'KC');
export const TUCKER = playerRef(108, 'Justin Tucker', 'K', 'BAL');
export const NINERS = playerRef(109, 'San Francisco 49ers', 'DST', 'SF');
export const COOK = playerRef(110, 'James Cook', 'RB', 'BUF');
export const OLAVE = playerRef(111, 'Chris Olave', 'WR', 'NO');
/** Unrostered. */
export const MOSTERT = playerRef(201, 'Raheem Mostert', 'RB', 'MIA');
export const COLLINS = playerRef(202, 'Nico Collins', 'WR', 'HOU');

export function record(overrides: Partial<ReplayRecord> = {}): ReplayRecord {
  return { wins: 3, losses: 1, ties: 0, rank: 2, points_for: 478.34, points_against: 431.1, place: null, ...overrides };
}

/** One team's line on a scoreboard: unplayed (points null) unless overridden. */
export function side(teamId: number, overrides: Partial<ReplaySide> = {}): ReplaySide {
  return { ...teamRef(teamId), projected: 110.5, points: null, record: record(), ...overrides };
}

/** A PlayerRow in the current week: healthy, not yet played. */
export function playerRow(player: ReplayPlayerRef, overrides: Partial<ReplayPlayerRow> = {}): ReplayPlayerRow {
  return {
    player, opponent: 'DEN', bye: false, out: false, projection: 14.2, points: null, ros_ppg: 13.8, value: 31.5,
    rank: 20, pos_rank: `${player.position}8`, adp: 24.5, pre_rank: 22, expert_rank: 8, news: null, games: 4, ppg: 15.1,
    trade_locked: false, defense: null,
    ...overrides,
  };
}

export function rosterRow(player: ReplayPlayerRef, slot: ReplaySlot, overrides: Partial<ReplayRosterRow> = {}): ReplayRosterRow {
  return { ...playerRow(player), slot, starting: slot !== 'BN', ...overrides };
}

/** Usage over `games` games (a receiver's line unless overridden). */
export function usage(overrides: Partial<ReplayUsage> = {}): ReplayUsage {
  return { games: 3, snap_pct: 72.4, targets: 6.3, target_share: 0.241, carries: 0.7, ...overrides };
}

/** A season stat line: 4 games, no box score unless overridden. */
export function statLine(overrides: Partial<ReplayStatLine> = {}): ReplayStatLine {
  return { span: 'season', week: null, games: 4, points: 60.4, projection: null, box: null, ...overrides };
}

/** A waiver-wire row: a free agent with no usage yet unless overridden. */
export function wireRow(player: ReplayPlayerRef, overrides: Partial<ReplayWireRow> = {}): ReplayWireRow {
  return { ...playerRow(player), status: 'free_agent', owner: null, season_points: 60.4, last_points: 12.3, usage: null,
    line: statLine(), ...overrides };
}

/** The user's roster in week 5: nine starters in slot order, then a bench with a bye and an injury. */
export function team(overrides: Partial<ReplayTeam> = {}): ReplayTeam {
  return {
    team: teamRef(USER_TEAM_ID), week: 5, played: false, manual_lineup: false, projected_total: 118.4, actual_total: null,
    record: record(), bench_total: null, bench_recorded: false,
    results: [
      rosterRow(MAHOMES, 'QB', { projection: 21.3, opponent: 'MIN' }),
      rosterRow(MCCAFFREY, 'RB', { projection: 22.8, opponent: 'DAL', rank: 1, pos_rank: 'RB1' }),
      rosterRow(ETIENNE, 'RB', { opponent: 'BUF' }),
      rosterRow(HILL, 'WR', { projection: 19.6, opponent: 'NYG' }),
      rosterRow(NACUA, 'WR', { opponent: 'PHI', trade_locked: true }),
      rosterRow(KELCE, 'TE', { opponent: 'MIN' }),
      rosterRow(PACHECO, 'FLEX', { opponent: 'MIN' }),
      rosterRow(TUCKER, 'K', { projection: 8.4, opponent: 'PIT' }),
      rosterRow(NINERS, 'DST', { projection: 7.9, opponent: 'DAL' }),
      rosterRow(COOK, 'BN', { opponent: 'BYE', bye: true, projection: 0 }),
      rosterRow(OLAVE, 'BN', { opponent: 'NE', out: true, projection: 0 }),
    ],
    ir: [],
    ...overrides,
  };
}

/** The game at a glance. `summary({ phase: 'waivers', next_action: nextAction('wednesday') })` and so on. */
export function summary(overrides: Partial<ReplaySummary> = {}): ReplaySummary {
  return {
    id: GAME_ID, name: 'Sunday Replay', season: 2023, phase: 'open', week: 5, end_week: 16, user_team_id: USER_TEAM_ID,
    settings: {
      teams: 12, scoring: 'half', starters: { QB: 1, RB: 2, WR: 2, TE: 1, FLEX: 1, K: 1, DST: 1 }, bench: 6, roster_size: 15,
      draft_slot: 4, waiver_mode: 'wednesday', difficulty: 'normal', regular_season_weeks: 14, playoff_teams: 4,
      trade_deadline_week: 11,
    },
    teams: TEAMS, on_the_clock: null, next_action: nextAction('play'), record: record(),
    matchup: {
      week: 5, playoff: false, label: null, you: side(USER_TEAM_ID, { projected: 118.4 }),
      opponent: side(OPPONENT_TEAM_ID, { projected: 104.75, record: record({ wins: 2, losses: 2, rank: 6 }) }),
    },
    pending: { claims: 0, offers: 0 }, champion: null, updated_at: '2026-11-20T12:00:00',
    ir_blocked: [], has_injuries: true, trades_open: true, rank_source: 'experts',
    ...overrides,
  };
}

const ACTION_LABELS = { pick: 'Make your pick', wednesday: 'Sim to Wednesday', play: 'Play week 5', done: 'Season complete' };

/** next_action with the server's label for a step. */
export function nextAction(step: keyof typeof ACTION_LABELS): ReplaySummary['next_action'] {
  return { step, label: ACTION_LABELS[step] };
}

/** The game during the draft: week 0, the user on the clock, no matchup yet. */
export function draftSummary(overrides: Partial<ReplaySummary> = {}): ReplaySummary {
  return summary({ phase: 'draft', week: 0, on_the_clock: USER_TEAM_ID, next_action: nextAction('pick'), matchup: null,
    record: record({ wins: 0, losses: 0, rank: 4, points_for: 0, points_against: 0 }), ...overrides });
}

export function event(id: number, kind: ReplayEventKind, text: string, overrides: Partial<ReplayEvent> = {}): ReplayEvent {
  return { id, week: 5, kind, team: null, text, players: [], ...overrides };
}

/** What playing week 5 returns: the user's win first, then a CPU result. */
export const RESULT_EVENTS: ReplayEvent[] = [
  event(31, 'result', 'Your Team beat Sunday Scaries 121.46-98.20', { team: teamRef(USER_TEAM_ID) }),
  event(32, 'result', 'Gridiron Ghosts beat Blitz Brigade 110.02-109.88', { team: teamRef(1) }),
];

/** What a POST answers: {game, events}. Spread extras on top (`{ ...actionResult(), verdict }`). */
export function actionResult(game: ReplaySummary = summary(), events: ReplayEvent[] = []): ReplayActionResult {
  return { game, events };
}

/**
 * Props for rendering one screen on its own: `<TeamView {...viewProps({ openPlayer: vi.fn() })} />`. Its `act`
 * sends the request and resolves with the answer (undefined on failure) like the hub's, but updates nothing.
 */
export function viewProps(overrides: Partial<ReplayViewProps> = {}): ReplayViewProps {
  return { game: summary(), version: 0, act: (run) => run().catch(() => undefined), isBusy: false, actError: null,
    openPlayer: () => undefined, ...overrides };
}

/**
 * Stands in for the hub when a test needs the screen to reload after an action: `act` sends the request, bumps
 * `version` on success and swallows a failure. Spread into viewProps: `viewProps({ game, ...useTestHub() })`.
 * It has none of the real hub's busy handling, so the screen's own guards are what is under test.
 */
export function useTestHub(): Pick<ReplayViewProps, 'version' | 'act'> {
  const [version, setVersion] = useState(0);
  const act: ReplayViewProps['act'] = async (run) => {
    try {
      const result = await run();
      setVersion((n) => n + 1);
      return result;
    } catch {
      return undefined;
    }
  };
  return { version, act };
}

/** GET players/{id}: weeks 1..5, a bye in week 3, the current week unplayed (usage only in weeks he played). */
export function playerDetail(player: ReplayPlayerRef = MOSTERT, overrides: Partial<ReplayWireRow> = {}): ReplayPlayerDetail {
  const row = wireRow(player, overrides);
  return {
    player: row,
    weeks: [1, 2, 3, 4, 5].map((week) => ({
      week, played: week < 5, team: player.team, opponent: week === 3 ? 'BYE' : 'DEN', bye: week === 3, out: false,
      projection: week === 3 ? 0 : 12.5, points: week === 3 || week === 5 ? null : 10 + week, ros_ppg: row.ros_ppg,
      value: row.value, rank: row.rank, pos_rank: row.pos_rank, expert_rank: row.expert_rank, news: null, injury: null,
      usage: week === 3 || week === 5 ? null : usage({ games: 1, snap_pct: 60 + week, targets: week, target_share: week / 20, carries: 10 }),
      box: null, defense: null, away: false,
    })),
    upcoming: [],
    defense_basis: { weeks: 4, prior: false },
  };
}

export function gameListing(overrides: Partial<ReplayGameListing> = {}): ReplayGameListing {
  return { id: GAME_ID, name: 'Sunday Replay', season: 2023, phase: 'open', week: 5, teams: 12, scoring: 'half', champion: null,
    created_at: '2026-11-01T09:00:00', updated_at: '2026-11-20T12:00:00', ...overrides };
}

export function readiness(season: number, ready = true): ReplayReadiness {
  return { season, ready, has_stats: ready, has_schedule: ready, has_kdst: ready, adp_players: ready ? 240 : 0,
    projection_weeks: ready ? 18 : 0, kickers: ready ? 32 : 0, defenses: ready ? 32 : 0, injury_weeks: ready ? 18 : 0,
    last_week: 18 };
}

/** GET /seasons: 2022 and 2023 ready, 2024 not, nothing importing. */
export function seasons(overrides: Partial<ReplaySeasons> = {}): ReplaySeasons {
  return { results: [readiness(2022), readiness(2023), readiness(2024, false)], prepare: null, ...overrides };
}
