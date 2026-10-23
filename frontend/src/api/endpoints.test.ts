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
    await sendChat([{ role: 'user', content: 'hi' }]);
    await getIntegrationsStatus();
    await getYahooAuthUrl();
    await exchangeYahooCode('abc');
    await getYahooLeagues();
    await importYahooLeague('449.l.12345');

    expect(api.calls.map((call) => [call.method, call.url.pathname, call.body])).toEqual([
      ['POST', '/api/trade/evaluate', { give: [1], get: [2, 3], settings: SETTINGS }],
      ['GET', '/api/agent/tools', undefined],
      ['POST', '/api/agent/tools/get_player_profile', { arguments: { player: 'Bijan Robinson' } }],
      ['POST', '/api/agent/chat', { messages: [{ role: 'user', content: 'hi' }] }],
      ['GET', '/api/integrations/status', undefined],
      ['GET', '/api/integrations/yahoo/auth-url', undefined],
      ['POST', '/api/integrations/yahoo/exchange', { code: 'abc' }],
      ['GET', '/api/integrations/yahoo/leagues', undefined],
      ['POST', '/api/integrations/yahoo/leagues/449.l.12345/import', undefined],
    ]);
  });
});
