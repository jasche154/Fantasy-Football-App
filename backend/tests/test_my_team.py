"""The user's imported Yahoo team: rules, best lineup, league waivers and trades, API, AI tool."""
import json

import pytest

from database.models import League, PlatformConnection, Roster, Team, User
from services import my_team as mt
from services import valuation as valuation_module
from services import weekly_rankings as weekly
from tests.test_injury_model import _seed_week
from tools.definitions import TOOLS

RULES = {"scoring": "ppr", "num_teams": 12, "starters": {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1},
         "bench": 4, "superflex": False, "week": 7}


def _seed_league(session, ids, mine, theirs, name="Family League"):
    user = User(email=f"{name}@local", hashed_password="x")
    session.add(user)
    session.flush()
    connection = PlatformConnection(user_id=user.id, platform="yahoo")
    session.add(connection)
    session.flush()
    league = League(platform_connection_id=connection.id, platform_league_id=f"key-{name}", name=name,
                    season=2026, scoring_settings=json.dumps(RULES))
    session.add(league)
    session.flush()
    for team_name, is_mine, roster in (("My Squad", True, mine), ("Rival", False, theirs)):
        team = Team(league_id=league.id, platform_team_id=f"{name}-{team_name}", name=team_name,
                    owner_display_name=team_name, is_users_team=is_mine)
        session.add(team)
        session.flush()
        for player, slot in roster:
            session.add(Roster(team_id=team.id, player_id=ids[player], week=7, roster_slot=slot))
    session.commit()
    weekly._cache.clear()
    valuation_module._cache.clear()
    return league


MINE = [("QB Player 01", "QB"), ("RB Player 20", "RB"), ("RB Player 01", "BN"), ("WR Player 01", "WR"),
        ("TE Player 01", "TE"), ("WR Player 05", "W/R/T"), ("RB Player 05", "IR")]
THEIRS = [("QB Player 02", "QB"), ("RB Player 02", "RB"), ("WR Player 02", "WR"), ("TE Player 02", "TE"),
          ("WR Player 03", "W/R/T"), ("RB Player 03", "BN")]


@pytest.fixture
def league(session, seeded):
    ids = _seed_week(session)
    from database.models import Player

    ids = {p.full_name: p.id for p in session.query(Player).all()}
    return _seed_league(session, ids, MINE, THEIRS), ids


def test_league_rules_become_settings(league):
    row, _ = league
    settings = mt.league_settings(row)
    assert (settings.scoring, settings.teams, settings.bench, settings.superflex) == ("ppr", 12, 4, False)
    assert dict(settings.starters) == RULES["starters"]


def test_find_league_by_id_name_or_only_one(session, league):
    row, _ = league
    assert mt.find_league(session, None)[0].id == row.id                    # only one league: picked
    assert mt.find_league(session, "family")[0].id == row.id
    assert mt.find_league(session, row.id)[1].name == "My Squad"
    with pytest.raises(mt.LeagueNotFound):
        mt.find_league(session, "nope")


def test_best_lineup_starts_the_benched_stud(session, league):
    row, ids = league
    team = mt.build_my_team(session, row.id)
    by_id = {p.player_id: p for p in team.rankings.players}
    best_ids = {pid for slot_ids in team.best.values() for pid in slot_ids}
    assert ids["RB Player 01"] in best_ids and ids["RB Player 05"] not in best_ids       # IR never starts
    change = next(c for c in team.changes if c.start.player_id == ids["RB Player 01"])
    assert change.bench_id is not None and change.bench_name and change.gain > 0
    assert change.start.projection >= change.bench_projection
    if change.bench_id not in by_id:
        assert change.bench_projection == 0.0                      # unprojected starter: sat first


def test_league_waivers_skip_everyone_rostered_in_the_league(session, league):
    row, ids = league
    _, _, candidates = mt.league_waivers(session, row.id, limit=100)
    rostered = {ids[name] for name, _ in MINE + THEIRS}
    assert candidates and not {c.player.player_id for c in candidates} & rostered
    assert candidates[0].reasons[0] == "Available in your league."


def test_league_trades_only_target_the_other_teams_roster(session, league):
    row, ids = league
    _, by_team = mt.league_trades(session, row.id)
    theirs = {ids[name] for name, _ in THEIRS}
    for result in by_team:
        assert result.team.name == "Rival"
        assert all(o.get.player_id in theirs for o in result.offers)


def test_my_team_api(client, session, league):
    row, ids = league
    leagues = client.get("/api/my-team/leagues").json()["leagues"]
    assert leagues == [{"id": row.id, "key": "key-Family League", "name": "Family League", "season": 2026,
                        "team": "My Squad", "scoring": "ppr", "teams": 12, "starters": RULES["starters"], "week": 7}]
    body = client.get(f"/api/my-team/{row.id}").json()
    assert {r["player"]["id"] for r in body["roster"]} == {ids[name] for name, _ in MINE}
    assert body["lineup"]["best_total"] >= body["lineup"]["current_total"]
    assert any(c["start"]["id"] == ids["RB Player 01"] for c in body["lineup"]["changes"])
    assert client.get(f"/api/my-team/{row.id}/waivers").status_code == 200
    assert client.get(f"/api/my-team/{row.id}/trades").json()["perspective"].startswith("give")
    assert client.get("/api/my-team/999").status_code == 404


def test_ai_tools_use_the_imported_team(session, league):
    row, ids = league
    mine = TOOLS.call(session, "get_my_team", {})
    assert mine["team"] == "My Squad" and len(mine["roster"]) == len(MINE)
    assert any("RB Player 01" in change for change in mine["lineup_changes"])
    free = TOOLS.call(session, "get_league_waivers", {"league": "Family"})
    assert free["results"] and all("RB Player 01" not in r["player"] for r in free["results"])
    assert "teams" in TOOLS.call(session, "find_league_trades", {})


def test_agent_prompt_mentions_imported_leagues(session, league):
    from services.agent import league_context

    assert '{"league": "Family League", "team": "My Squad"}' in league_context(session)


def test_matchup_simulates_both_lineups_and_picks_the_best_strategy(session, league, monkeypatch, client):
    from integrations import yahoo
    from services import matchup as mu

    row, ids = league
    team = lambda key, name: {"team": [[{"team_key": key}, {"name": name}],  # noqa: E731
                                       {"win_probability": 0.4, "team_points": {"total": "0"},
                                        "team_projected_points": {"total": "100"}}]}
    board = {"league": [{}, {"scoreboard": {"0": {"matchups": {"0": {"matchup": {"0": {"teams": {
        "0": team("Family League-My Squad", "My Squad"), "1": team("Family League-Rival", "Rival"), "count": 2}}}},
        "count": 1}}}}]}
    monkeypatch.setattr(mu.yahoo_sync, "fresh_token", lambda *a, **k: "token")
    monkeypatch.setattr(mu.yahoo, "api_get", lambda http, token, path: board)
    body = client.get(f"/api/my-team/{row.id}/matchup").json()
    assert body["me"]["team"] == "My Squad" and body["opponent"]["team"] == "Rival"
    assert 0.0 <= body["win_probability"] <= 1.0 and body["yahoo_win_probability"] == 0.4
    assert set(body["strategies"]) == {"expected", "upside", "safe"}
    assert body["strategies"][body["recommended"]]["win_probability"] == max(
        s["win_probability"] for s in body["strategies"].values())
    assert "RB Player 01" in body["changes"]["start"]                       # the benched stud starts
    updated = body["updated"]
    assert updated["mode"] == body["recommended"] and any(r["name"] == "RB Player 01" for r in updated["starters"])
    assert updated["projected"] == pytest.approx(sum(r["projection"] for r in updated["starters"]), abs=0.2)
    on_bench = {r["player_id"] for r in body["bench"]}
    assert ids["RB Player 05"] in on_bench and not on_bench & {r["player_id"] for r in updated["starters"]}
    assert [r["projection"] for r in body["bench"]] == sorted((r["projection"] for r in body["bench"]), reverse=True)
    monkeypatch.setattr(mu.yahoo, "api_get", lambda http, token, path: {"league": []})
    assert client.get(f"/api/my-team/{row.id}/matchup").status_code == 404   # no matchup (bye)


def test_playoff_outlook_uses_the_league_playoff_weeks(session, league, client):
    row, ids = league
    body = client.get(f"/api/my-team/{row.id}/playoffs").json()
    assert body["weeks"] == [16, 17, 18] and body["source"] == "default"
    assert {p["player"]["id"] for p in body["players"]} <= {ids[name] for name, _ in MINE}
    assert set(body["lineups"]) == {"16", "17", "18"}
    row.scoring_settings = json.dumps({**json.loads(row.scoring_settings), "playoff_weeks": [15, 16, 17]})
    session.commit()
    again = client.get(f"/api/my-team/{row.id}/playoffs").json()
    assert again["weeks"] == [15, 16, 17] and again["source"] == "league"
    for player in again["players"]:
        assert [c["week"] for c in player["weeks"]] == [15, 16, 17]
        assert player["playoff_total"] == pytest.approx(sum(c["projection"] for c in player["weeks"]), abs=0.2)
