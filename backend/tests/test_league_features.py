"""Power rankings, playoff odds, manager tendencies, the playoff planner, player news and comparisons."""
import httpx
import pytest

from integrations import sleeper_news, yahoo
from services import league_insights as li
from services.playoffs import playoff_outlook
from tests.test_my_team import league  # noqa: F401  (fixture)
from tools.definitions import TOOLS


def _team(key, name, wins, losses, pf, moves, trades):
    return {"team": [[{"team_key": key}, {"team_id": key[-1]}, {"name": name}, {"number_of_moves": moves},
                      {"number_of_trades": str(trades)}],
                     {"team_points": {"total": str(pf)}},
                     {"team_standings": {"rank": "1", "outcome_totals": {"wins": str(wins), "losses": str(losses),
                                                                        "ties": 0},
                                         "points_for": str(pf), "points_against": 500.0}}]}


def test_parse_standings_reads_record_points_and_activity():
    content = {"league": [{"league_key": "l"}, {"standings": [{"teams": {
        "0": _team("l.t.1", "Alpha", 6, 1, 876.18, 8, 3), "1": _team("l.t.2", "Beta", 0, 7, 452.0, 0, 0), "count": 2}}]}]}
    rows = yahoo.parse_standings(content)
    assert [(r["name"], r["wins"], r["losses"], r["points_for"], r["moves"], r["trades"]) for r in rows] == [
        ("Alpha", 6, 1, 876.18, 8, 3), ("Beta", 0, 7, 452.0, 0, 0)]


def test_parse_transactions_reads_trades_and_adds():
    def player(pid, name, pos, data):
        return {"player": [[{"player_key": f"p.{pid}"}, {"player_id": pid}, {"name": {"full": name}},
                            {"display_position": pos}], {"transaction_data": data}]}

    content = {"league": [{}, {"transactions": {"0": {"transaction": [
        {"type": "trade", "status": "successful", "timestamp": "100"},
        {"players": {"0": player("1", "A Back", "RB", [{"type": "trade", "source_team_key": "t1",
                                                         "destination_team_key": "t2"}]),
                     "1": player("2", "B Wide", "WR", {"type": "trade", "source_team_key": "t2",
                                                        "destination_team_key": "t1"}), "count": 2}}]},
        "count": 1}}]}
    [trade] = yahoo.parse_transactions(content)
    assert trade["type"] == "trade" and trade["timestamp"] == 100
    assert [(m["name"], m["position"], m["from_team"], m["to_team"]) for m in trade["moves"]] == [
        ("A Back", "RB", "t1", "t2"), ("B Wide", "WR", "t2", "t1")]
    assert yahoo.parse_transactions({"league": []}) == []


def test_simulated_odds_favor_strong_teams_and_respect_the_cutoff():
    teams = ["a", "b", "c", "d"]
    games = [("a", "b"), ("c", "d")] * 6
    odds = li.simulate_season(teams, {"a": 130, "b": 80, "c": 100, "d": 100}, {t: 3 for t in teams},
                              {t: 700 for t in teams}, games, playoff_teams=2, sims=4000)
    assert odds["a"]["playoff_odds"] > 0.9 > odds["c"]["playoff_odds"] > odds["b"]["playoff_odds"]
    assert sum(o["playoff_odds"] for o in odds.values()) == pytest.approx(2.0, abs=0.01)
    done = li.simulate_season(teams, {t: 100 for t in teams}, {"a": 9, "b": 8, "c": 1, "d": 0},
                              {t: 0 for t in teams}, [], playoff_teams=2, sims=100)
    assert done["a"]["playoff_odds"] == done["b"]["playoff_odds"] == 1.0 and done["c"]["playoff_odds"] == 0.0


def test_tendencies_label_traders_waiver_regulars_and_quiet_managers():
    standings = [{"team_key": "t1", "moves": 8, "trades": 3}, {"team_key": "t2", "moves": 14, "trades": 0},
                 {"team_key": "t3", "moves": 1, "trades": 0}, {"team_key": "t4", "moves": 5, "trades": 0}]
    transactions = [{"type": "trade", "moves": [
        {"type": "trade", "position": "RB", "from_team": "t2", "to_team": "t1"},
        {"type": "trade", "position": "WR", "from_team": "t1", "to_team": "t2"}]},
        {"type": "add", "moves": [{"type": "add", "position": "TE", "from_team": None, "to_team": "t2"}]}]
    habits = li.tendencies(standings, transactions)
    assert habits["t1"]["label"] == "Active trader" and habits["t1"]["trades_for"] == ["RB"]
    assert habits["t2"]["label"] == "Waiver regular" and habits["t2"]["picks_up"] == ["TE"]
    assert habits["t3"]["label"] == "Quiet" and habits["t4"]["label"] is None


def test_playoff_planner_lists_upgrades_over_the_weakest_starter(session, league):
    row, _ = league
    outlook = playoff_outlook(session, row.id)
    assert isinstance(outlook["fixes"], list)
    for fix in outlook["fixes"]:
        bar = fix["weakest_starter"]["playoff_total"] if fix["weakest_starter"] else 0.0
        for option in fix["waivers"] + fix["trade_targets"]:
            assert option["playoff_total"] >= bar + 3.0 and option["gain"] >= 3.0
        assert all(o["owner"] is None for o in fix["waivers"])
        assert all(o["owner"] == "Rival" for o in fix["trade_targets"])


def test_news_parsing_keeps_headlines_drops_unsafe_links_and_sorts_newest_first():
    rows = [{"metadata": {"title": "Old note", "url": "https://example.com/a"}, "source": "rotowire", "published": 1000},
            {"metadata": {"title": "New note", "description": "x " * 500, "url": "javascript:alert(1)"},
             "source": "fantasy_pros", "published": 5000},
            {"metadata": {"description": "no title"}, "published": 9000}, "junk"]
    items = sleeper_news.parse_news(rows)
    assert [i["title"] for i in items] == ["New note", "Old note"]
    assert items[0]["url"] is None and items[1]["url"] == "https://example.com/a"
    assert items[0]["source"] == "FantasyPros" and len(items[0]["summary"]) <= sleeper_news.MAX_TEXT + 1
    assert sleeper_news.parse_news({"not": "a list"}) == []


def test_player_news_route_and_tool(client, session, seeded, monkeypatch):
    from database.models import Player

    player = session.get(Player, seeded["chase"])
    player.sleeper_player_id = "7564"
    session.commit()
    sleeper_news._cache.clear()

    def handler(request):
        assert request.url.path == "/players/nfl/7564/news"
        return httpx.Response(200, json=[{"metadata": {"title": "Chase limited Wednesday"}, "source": "rotowire",
                                          "published": 1793000000000}])

    real_client = httpx.Client
    monkeypatch.setattr(sleeper_news.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler)))
    body = client.get(f"/api/players/{seeded['chase']}/news").json()
    assert body["items"][0]["title"] == "Chase limited Wednesday"
    tool = TOOLS.call(session, "get_player_news", {"player": "Ja'Marr Chase"})
    assert tool["items"][0]["title"] == "Chase limited Wednesday"
    assert client.get("/api/players/999999/news").status_code == 404


def test_compare_route_returns_games_and_summaries(client, seeded):
    ids = f"{seeded['rb1']},{seeded['rb2']}"
    body = client.get(f"/api/compare?ids={ids}").json()
    assert [p["player"]["id"] for p in body["players"]] == [seeded["rb1"], seeded["rb2"]]
    first = body["players"][0]
    assert first["games"] and first["summary"][0]["games"] == len(first["games"])
    assert {"fantasy_points", "expected_points", "targets", "carries", "season", "week"} <= set(first["games"][0])
    assert client.get("/api/compare?ids=x").status_code == 422
    assert client.get("/api/compare?ids=1,2,3,4,5").status_code == 422
    assert client.get(f"/api/compare?ids={ids}&seasons=1999").status_code == 404


def test_power_rankings_tool_needs_a_league(session):
    with pytest.raises(ValueError, match="No Yahoo league imported"):
        TOOLS.call(session, "get_power_rankings", {})


def test_league_overview_endpoint_combines_standings_schedule_and_rosters(client, session, league, monkeypatch):
    from services import yahoo_sync

    row, _ = league
    mine, rival = "Family League-My Squad", "Family League-Rival"
    standings = {"league": [{}, {"standings": [{"teams": {
        "0": _team(mine, "My Squad", 5, 2, 800, 9, 2), "1": _team(rival, "Rival", 2, 5, 700, 1, 0), "count": 2}}]}]}
    empty = {"league": [{}, {"transactions": {"count": 0}}]}

    def board(_week):
        teams = {"0": {"team": [[{"team_key": mine}, {"name": "My Squad"}], {}]},
                 "1": {"team": [[{"team_key": rival}, {"name": "Rival"}], {}]}, "count": 2}
        return {"league": [{}, {"scoreboard": {"0": {"matchups": {"0": {"matchup": {"0": {"teams": teams}}},
                                                                   "count": 1}}}}]}

    def api_get(_http, _token, path):
        if path.endswith("/standings"):
            return standings
        if "/transactions" in path:
            return empty
        return board(int(path.rsplit("=", 1)[1]))

    monkeypatch.setattr(yahoo_sync, "fresh_token", lambda *a, **k: "token")
    monkeypatch.setattr(li.yahoo, "api_get", api_get)
    li._cache.clear()
    body = client.get(f"/api/my-team/{row.id}/league").json()
    teams = {t["team"]: t for t in body["teams"]}
    assert teams["My Squad"]["is_users_team"] and teams["My Squad"]["record"] == "5-2"
    assert teams["My Squad"]["tendencies"]["label"] == "Active trader"
    assert body["regular_season_weeks_left"] == 8              # weeks 8-15 before the default 16-18 playoffs
    assert teams["My Squad"]["playoff_odds"] + teams["Rival"]["playoff_odds"] == pytest.approx(
        min(body["playoff_teams"], 2), abs=0.01)
    assert sorted(t["power_rank"] for t in body["teams"]) == [1, 2]
