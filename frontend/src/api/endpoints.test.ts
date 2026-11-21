import { describe, expect, it } from 'vitest';
import { DEFAULT_LEAGUE_SETTINGS } from '../state/leagueSettings';
import { json, mockFetch } from '../test/mockFetch';
import {
  callAgentTool,
  evaluateTrade,
  exchangeYahooCode,
  getAgentTools,
  getIntegrationsStatus,
  getYahooAuthUrl,
  getYahooLeagues,
  importYahooLeague,
  sendChat,
} from './actions';
import {
  getBuyLow,
  getExpertRankings,
  getHealth,
  getLeaderboard,
  getLeaderboardMetrics,
  getMeta,
  getPlayer,
  getRankings,
  getRushingLeaders,
  getSellHigh,
  getTrending,
  searchPlayers,
} from './data';
import * as replay from './replay';

const SETTINGS = { ...DEFAULT_LEAGUE_SETTINGS, scoring: 'half' as const, superflex: true };
const LEAGUE_QUERY = 'scoring=half&teams=12&superflex=true&te_premium=0';

function captureAll() {
  return mockFetch([
    { path: /.*/, reply: json({ ok: true }) },
    { method: 'POST', path: /.*/, reply: json({ ok: true }) },
  ]);
}

describe('GET endpoints', () => {
  it('build the documented paths and params', async () => {
    const api = captureAll();
    await getHealth();
    await getMeta();
    await searchPlayers('bij', { position: 'RB', limit: 5 });
    await getPlayer(12, SETTINGS, 2024);
    await getRankings(SETTINGS, { position: 'WR', limit: 100 });
    await getLeaderboardMetrics();
    await getLeaderboard({ category: 'rushing', metric: 'ryoe_per_att', season: 2025, min_volume: 50, ascending: true });
    await getRushingLeaders({ metric: 'rush_yards_over_expected', season: 2025, min_attempts: 50 });
    await getBuyLow(SETTINGS, { position: 'RB', limit: 5 });
    await getSellHigh(SETTINGS);
    await getTrending('add', 10);
    await getExpertRankings({ position: 'TE', limit: 50 });

    expect(api.calls.map((call) => `${call.url.pathname}${call.url.search}`)).toEqual([
      '/api/health',
      '/api/meta',
      '/api/players/search?q=bij&position=RB&limit=5',
      `/api/players/12?season=2024&${LEAGUE_QUERY}`,
      `/api/rankings?position=WR&limit=100&${LEAGUE_QUERY}`,
      '/api/leaderboards/metrics',
      '/api/leaderboards?category=rushing&metric=ryoe_per_att&season=2025&min_volume=50&ascending=true',
      '/api/rankings/rushing?metric=rush_yards_over_expected&season=2025&min_attempts=50',
      `/api/insights/buy-low?position=RB&limit=5&${LEAGUE_QUERY}`,
      `/api/insights/sell-high?${LEAGUE_QUERY}`,
      '/api/insights/trending?type=add&limit=10',
      '/api/experts/rankings?position=TE&limit=50',
    ]);
  });
});

describe('action endpoints', () => {
  it('send the documented methods and bodies', async () => {
    const api = captureAll();
    await evaluateTrade({ give: [1], get: [2, 3], settings: SETTINGS });
    await getAgentTools();
    await callAgentTool('get_player_profile', { player: 'Bijan Robinson' });
    await sendChat({ messages: [{ role: 'user', content: 'hi' }], memory: [] });
    await getIntegrationsStatus();
    await getYahooAuthUrl();
    await exchangeYahooCode('abc');
    await getYahooLeagues();
    await importYahooLeague('449.l.12345');

    expect(api.calls.map((call) => [call.method, call.url.pathname, call.body])).toEqual([
      ['POST', '/api/trade/evaluate', { give: [1], get: [2, 3], settings: SETTINGS }],
      ['GET', '/api/agent/tools', undefined],
      ['POST', '/api/agent/tools/get_player_profile', { arguments: { player: 'Bijan Robinson' } }],
      ['POST', '/api/agent/chat', { messages: [{ role: 'user', content: 'hi' }], memory: [] }],
      ['GET', '/api/integrations/status', undefined],
      ['GET', '/api/integrations/yahoo/auth-url', undefined],
      ['POST', '/api/integrations/yahoo/exchange', { code: 'abc' }],
      ['GET', '/api/integrations/yahoo/leagues', undefined],
      ['POST', '/api/integrations/yahoo/leagues/449.l.12345/import', undefined],
    ]);
  });
});

describe('Replay League endpoints', () => {
  it('read every screen from the documented paths', async () => {
    const api = captureAll();
    await replay.getReplaySeasons();
    await replay.getReplayGames();
    await replay.getReplayGame(7);
    await replay.getReplayDraft(7, { position: 'RB', search: 'cook', limit: 25, offset: 50 });
    await replay.getReplayTeam(7);
    await replay.getReplayTeam(7, { team_id: 3, week: 2 });
    await replay.getReplayMatchup(7, { week: 4, team_id: 3 });
    await replay.getReplayScoreboard(7, 4);
    await replay.getReplayStandings(7);
    await replay.getReplaySchedule(7, 3);
    await replay.getReplayActivity(7, { kind: 'add,drop', limit: 20 });
    await replay.getReplayPlayers(7, { position: 'FLEX', search: 'mo', status: 'all', sort: 'points', limit: 50, offset: 0 });
    await replay.getReplayPlayer(7, 201);
    await replay.getReplayClaims(7);
    await replay.getReplayTrades(7);

    expect(api.calls.map((call) => `${call.method} ${call.url.pathname}${call.url.search}`)).toEqual([
      'GET /api/replay/seasons',
      'GET /api/replay/games',
      'GET /api/replay/games/7',
      'GET /api/replay/games/7/draft?position=RB&search=cook&limit=25&offset=50',
      'GET /api/replay/games/7/team',
      'GET /api/replay/games/7/team?team_id=3&week=2',
      'GET /api/replay/games/7/matchup?week=4&team_id=3',
      'GET /api/replay/games/7/scoreboard?week=4',
      'GET /api/replay/games/7/standings',
      'GET /api/replay/games/7/schedule?team_id=3',
      'GET /api/replay/games/7/activity?kind=add%2Cdrop&limit=20',
      'GET /api/replay/games/7/players?position=FLEX&search=mo&status=all&sort=points&limit=50&offset=0',
      'GET /api/replay/games/7/players/201',
      'GET /api/replay/games/7/claims',
      'GET /api/replay/games/7/trades',
    ]);
  });

  it('post every action with the documented body', async () => {
    const api = captureAll();
    const trade = { to_team: 9, give: [102], get: [201, 202], drops: [111] };
    await replay.prepareReplaySeason(2023);
    await replay.prepareReplaySeason(2023, true);
    await replay.createReplayGame({ season: 'random', teams: 10, scoring: 'ppr', waiver_mode: 'none', difficulty: 'hard' });
    await replay.deleteReplayGame(7);
    await replay.replayDraftPick(7, 102);
    await replay.replayDraftAuto(7);
    await replay.replayDraftAuto(7, true);
    await replay.replayDraftPick(7, 102, 21);
    await replay.replayDraftAuto(7, true, 21);
    await replay.setReplayLineup(7, { QB: [101], RB: [102, 103] });
    await replay.setReplayLineup(7, null);
    await replay.submitReplayClaim(7, { add: 201, drop: 111 });
    await replay.cancelReplayClaim(7, 5);
    await replay.reorderReplayClaims(7, [6, 5]);
    await replay.replayAddDrop(7, { add: 201 });
    await replay.replayDrop(7, 111);
    await replay.replayIrPlace(7, 111);
    await replay.replayIrActivate(7, 111);
    await replay.replayIrActivate(7, 111, { drop: 110 });
    await replay.replayIrActivate(7, 111, { swap: 103 });
    await replay.advanceReplay(7, 'wednesday', 5);
    await replay.evaluateReplayTrade(7, trade);
    await replay.proposeReplayTrade(7, trade);
    await replay.respondReplayOffer(7, 3, true, [111]);
    await replay.respondReplayOffer(7, 3, false);

    const games = '/api/replay/games';
    expect(api.calls.map((call) => [call.method, `${call.url.pathname}${call.url.search}`, call.body])).toEqual([
      ['POST', '/api/replay/seasons/2023/prepare', undefined],
      ['POST', '/api/replay/seasons/2023/prepare?force=true', undefined],
      ['POST', games, { season: 'random', teams: 10, scoring: 'ppr', waiver_mode: 'none', difficulty: 'hard' }],
      ['POST', `${games}/7/delete`, undefined],
      ['POST', `${games}/7/draft/pick`, { player_id: 102 }],
      ['POST', `${games}/7/draft/auto`, undefined],
      ['POST', `${games}/7/draft/auto?whole_draft=true`, undefined],
      ['POST', `${games}/7/draft/pick`, { player_id: 102, overall: 21 }],
      ['POST', `${games}/7/draft/auto?whole_draft=true&overall=21`, undefined],
      ['POST', `${games}/7/lineup`, { starters: { QB: [101], RB: [102, 103] } }],
      ['POST', `${games}/7/lineup`, { starters: null }],
      ['POST', `${games}/7/claims`, { add: 201, drop: 111 }],
      ['POST', `${games}/7/claims/5/cancel`, undefined],
      ['POST', `${games}/7/claims/reorder`, { ids: [6, 5] }],
      ['POST', `${games}/7/add-drop`, { add: 201 }],
      ['POST', `${games}/7/drop`, { player_id: 111 }],
      ['POST', `${games}/7/ir/place`, { player_id: 111 }],
      ['POST', `${games}/7/ir/activate`, { player_id: 111 }],
      ['POST', `${games}/7/ir/activate`, { player_id: 111, drop: 110 }],
      ['POST', `${games}/7/ir/activate`, { player_id: 111, swap: 103 }],
      ['POST', `${games}/7/advance`, { step: 'wednesday', week: 5 }],
      ['POST', `${games}/7/trades/evaluate`, trade],
      ['POST', `${games}/7/trades/propose`, trade],
      ['POST', `${games}/7/trades/offers/3/respond`, { accept: true, drops: [111] }],
      ['POST', `${games}/7/trades/offers/3/respond`, { accept: false, drops: [] }],
    ]);
  });
});
