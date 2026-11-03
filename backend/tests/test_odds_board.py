"""The Odds page reads stored lines and props only: it must never reach The Odds API."""
import json
from datetime import date, datetime, timezone

import httpx
import pytest

from database.models import NflGame, WeeklySourceProjection
from integrations import odds_api
from services import weekly_refresh
from services.odds_board import NO_PROPS_NOTE, default_week, odds_board

SEASON = 2026


@pytest.fixture
def games(session, seeded):
    session.add_all([
        # 1:00 pm Eastern on Nov 8 2026 (standard time) = 18:00 UTC.
        NflGame(game_id="2026_10_BUY_CIN", season=SEASON, week=10, game_type="REG", gameday="2026-11-08",
                gametime="13:00", home_team="CIN", away_team="BUY", spread_line=3.5, total_line=47.5),
        NflGame(game_id="2026_10_SEL_NYJ", season=SEASON, week=10, game_type="REG", gameday="2026-11-08",
                gametime="16:25", home_team="NYJ", away_team="SEL", spread_line=None, total_line=None),
        NflGame(game_id="2026_09_CIN_NYJ", season=SEASON, week=9, game_type="REG", gameday="2026-11-01",
                gametime="13:00", home_team="NYJ", away_team="CIN", spread_line=-2.0, total_line=44.0,
                home_score=20, away_score=24),
    ])
    session.add_all([
        WeeklySourceProjection(season=SEASON, week=10, source="vegas_props", player_id=seeded["chase"],
                               player_name="Ja'Marr Chase", position="WR", team="CIN", opponent="BUY",
                               stats=json.dumps({"rec": 6.5, "rec_yds": 81.2, "scrimmage_tds": 0.55, "junk": "x"}),
                               fetched_at=datetime(2026, 11, 8, 16, 30)),
        WeeklySourceProjection(season=SEASON, week=10, source="vegas_props", player_id=seeded["buy"],
                               player_name="Buy Low Guy", position="WR", team="BUY", opponent="CIN",
                               stats=json.dumps({"rec": 5.5}), fetched_at=datetime(2026, 11, 8, 16, 0)),
        WeeklySourceProjection(season=SEASON, week=10, source="espn", player_id=seeded["chase"],
                               player_name="Ja'Marr Chase", position="WR", team="CIN", stats="{}"),
    ])
    session.commit()


@pytest.fixture
def no_outside_calls(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("the Odds page must not make outside calls")

    monkeypatch.setattr(odds_api.OddsApiClient, "__init__", boom)
    monkeypatch.setattr(httpx.Client, "send", boom)
    monkeypatch.setattr(weekly_refresh, "refresh_sources", boom)


def test_board_lines_implied_totals_and_props(session, games):
    board = odds_board(session, SEASON, 10)
    assert [(g["away_team"], g["home_team"]) for g in board["games"]] == [("BUY", "CIN"), ("SEL", "NYJ")]
    first, second = board["games"]
    assert first["home_implied"] == 25.5 and first["away_implied"] == 22.0      # (47.5 +/- 3.5) / 2
    assert first["kickoff"] == "2026-11-08T18:00:00+00:00"
    assert [p["name"] for p in first["props"]] == ["Buy Low Guy", "Ja'Marr Chase"]   # away team first
    assert first["props"][1]["stats"] == {"rec": 6.5, "rec_yds": 81.2, "scrimmage_tds": 0.55}
    assert first["props_fetched_at"] == "2026-11-08T16:30:00+00:00"
    assert first["props_hours_before_kickoff"] == 1.5
    assert second["home_implied"] is None and second["props"] == [] and second["props_fetched_at"] is None
    assert board["props_count"] == 2 and board["note"] is None


def test_board_without_props_explains_why(session, games):
    board = odds_board(session, SEASON, 9)
    assert board["games"][0]["final"] and board["games"][0]["away_score"] == 24
    assert board["props_count"] == 0 and board["note"] == NO_PROPS_NOTE
    assert "2 hours of kickoff" in NO_PROPS_NOTE


def test_default_week_is_the_first_with_games_left(session, games):
    assert default_week(session, SEASON, today=date(2026, 11, 2)) == 10
    assert default_week(session, 2030) == 18


def test_odds_endpoint_never_calls_the_odds_api(client, games, no_outside_calls):
    body = client.get("/api/odds", params={"season": SEASON, "week": 10}).json()
    assert body["week"] == 10 and body["props_count"] == 2
    default = client.get("/api/odds").json()
    assert default["season"] == SEASON and default["week"] == 10


def test_odds_endpoint_validates(client, games, no_outside_calls):
    assert client.get("/api/odds", params={"week": 0}).status_code == 422
    assert client.get("/api/odds", params={"week": 19}).status_code == 422
    assert client.get("/api/odds", params={"season": 1900}).status_code == 422
    assert client.get("/api/odds", params={"week": "x"}).status_code == 422


def test_odds_endpoint_without_a_schedule(client, no_outside_calls):
    assert client.get("/api/odds").status_code == 503


def test_fetched_at_is_reported_in_utc(session, games):
    props = odds_board(session, SEASON, 10)["games"][0]["props"]
    assert all(datetime.fromisoformat(p["fetched_at"]).tzinfo == timezone.utc for p in props)
