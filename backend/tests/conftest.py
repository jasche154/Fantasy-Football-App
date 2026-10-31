"""Shared fixtures: an in-memory SQLite database seeded with a small synthetic league.

Tests never touch backend/database/fantasy.db or read backend/.env.
"""
import os

os.environ["FANTASY_ENV_FILE"] = ""
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["WEEKLY_AUTO_REFRESH"] = "false"     # never reach Yahoo/ESPN/The Odds API from tests
os.environ["AUTO_UPDATE"] = "false"             # no background data loads from tests

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from config import get_settings
from database.db import get_session
from database.models import (
    Base, ExpertRanking, Player, PlayerExpectedPointsWeekly, PlayerReceivingAdvancedWeekly,
    PlayerRushingAdvancedWeekly, PlayerSnapCountsWeekly, PlayerStatsWeekly,
)
from services import valuation as valuation_module

CURRENT, PRIOR = 2026, 2025
CURRENT_WEEKS = range(1, 7)
PRIOR_WEEKS = range(1, 18)
POOL_SIZES = {"QB": 20, "RB": 50, "WR": 60, "TE": 25}
BASE_PPG = {"QB": 24.0, "RB": 22.0, "WR": 21.0, "TE": 15.0}
STEP = {"QB": 0.5, "RB": 0.35, "WR": 0.3, "TE": 0.45}


def stat_row(player_id: int, season: int, week: int, position: str, points: float, receptions: int = 0,
             tds: int = 0, season_type: str = "REG") -> dict:
    """A player-week whose PPR points equal `points` (TDs are counted inside `points`)."""
    yardage_points = points - receptions - 6 * tds
    row = {"player_id": player_id, "season": season, "week": week, "season_type": season_type,
           "opponent_team": "OPP", "receptions": receptions, "targets": receptions + 2,
           "target_share": 0.2 if receptions else None,
           "fantasy_points_ppr": points, "fantasy_points": points - receptions}
    if position == "QB":
        row.update(passing_yards=round(yardage_points / 0.04), passing_tds=tds, attempts=35)
    elif position == "RB":
        row.update(rushing_yards=round(yardage_points / 0.1), rushing_tds=tds, carries=18)
    else:
        row.update(receiving_yards=round(yardage_points / 0.1), receiving_tds=tds)
    return row


def expected_row(player_id: int, season: int, week: int, expected: float, receptions: int = 0,
                 expected_tds: float = 0.0) -> dict:
    return {"player_id": player_id, "season": season, "week": week, "total_fantasy_points_exp": expected,
            "receptions_exp": receptions, "pass_touchdown_exp": 0.0, "total_touchdown_exp": expected_tds}


def _add_player(session: Session, name: str, position: str, team: str = "AAA", **ids) -> int:
    player = Player(full_name=name, position=position, nfl_team=team, gsis_id=f"00-{name}-{team}", **ids)
    session.add(player)
    session.flush()
    return player.id


def _seed_pool(session: Session, stats: list, expected: list, snaps: list) -> None:
    for position, size in POOL_SIZES.items():
        receptions = 0 if position == "QB" else 4
        for i in range(size):
            pid = _add_player(session, f"{position} Player {i + 1:02d}", position)
            ppg = round(BASE_PPG[position] - STEP[position] * i, 1)
            for week in CURRENT_WEEKS:
                stats.append(stat_row(pid, CURRENT, week, position, ppg, receptions))
                expected.append(expected_row(pid, CURRENT, week, ppg, receptions))
                snaps.append({"player_id": pid, "season": CURRENT, "week": week, "offense_pct": 0.7})
            for week in PRIOR_WEEKS:
                stats.append(stat_row(pid, PRIOR, week, position, ppg, receptions))


def _seed_specials(session: Session, stats: list, expected: list, snaps: list) -> dict[str, int]:
    """Players with clear buy-low / sell-high profiles and tricky names."""
    ids = {
        "buy": _add_player(session, "Buy Low Guy", "WR", "BUY", sleeper_player_id="111"),
        "sell": _add_player(session, "Sell High Guy", "RB", "SEL", fantasypros_id="9001"),
        "chase": _add_player(session, "Ja'Marr Chase", "WR", "CIN", yahoo_player_id="555"),
        "mw1": _add_player(session, "Mike Williams", "WR", "NYJ"),
        "mw2": _add_player(session, "Mike Williams", "WR", "LAC"),
    }
    for week in CURRENT_WEEKS:
        # Buy-low: usage worth 20 PPR pts/game, only scoring 12; usage rising late.
        stats.append(stat_row(ids["buy"], CURRENT, week, "WR", 12.0, receptions=5))
        expected.append(expected_row(ids["buy"], CURRENT, week, 19.0 if week <= 3 else 21.0, 5, 1.0))
        snaps.append({"player_id": ids["buy"], "season": CURRENT, "week": week,
                      "offense_pct": 0.7 if week <= 3 else 0.85})
        # Sell-high: touchdown-driven 24 pts/game on 14 expected.
        stats.append(stat_row(ids["sell"], CURRENT, week, "RB", 24.0, receptions=2, tds=1))
        expected.append(expected_row(ids["sell"], CURRENT, week, 14.0, 2, 0.4))
        for key, points in (("chase", 19.0), ("mw1", 9.0), ("mw2", 8.0)):
            stats.append(stat_row(ids[key], CURRENT, week, "WR", points, receptions=5))
            expected.append(expected_row(ids[key], CURRENT, week, points, 5))
    # A postseason game from last year should never count toward regular-season numbers.
    stats.append(stat_row(ids["chase"], PRIOR, 19, "WR", 40.0, receptions=10, season_type="POST"))
    return ids


def _seed_ngs(session: Session, rb_a: int, rb_b: int, wr: int) -> None:
    rows = []
    for week in CURRENT_WEEKS:
        rows.append({"player_id": rb_a, "season": CURRENT, "week": week, "season_type": "REG",
                     "rush_attempts": 10, "rush_yards": 50, "rush_yards_over_expected": 10.0,
                     "rush_yards_over_expected_per_att": 1.0, "rush_pct_over_expected": 0.5,
                     "efficiency": 3.0})
        rows.append({"player_id": rb_b, "season": CURRENT, "week": week, "season_type": "REG",
                     "rush_attempts": 5 if week % 2 else 15, "rush_yards": 40,
                     "rush_yards_over_expected": 2.0 if week % 2 else 3.0,
                     "rush_yards_over_expected_per_att": 0.4 if week % 2 else 0.2,
                     "rush_pct_over_expected": 0.4, "efficiency": 4.0})
    # Official season total for rb_a includes 10 untracked attempts and NGS's own per-att value.
    rows.append({"player_id": rb_a, "season": CURRENT, "week": 0, "season_type": "REG",
                 "rush_attempts": 70, "rush_yards": 300, "rush_yards_over_expected": 60.0,
                 "rush_yards_over_expected_per_att": 0.95, "rush_pct_over_expected": 0.5, "efficiency": 3.0})
    session.execute(insert(PlayerRushingAdvancedWeekly), rows)
    session.execute(insert(PlayerReceivingAdvancedWeekly), [
        {"player_id": wr, "season": CURRENT, "week": w, "season_type": "REG", "targets": 8,
         "receptions": 6, "avg_separation": 3.0 + w * 0.1} for w in CURRENT_WEEKS])


def seed(session: Session) -> dict[str, int]:
    stats, expected, snaps = [], [], []
    _seed_pool(session, stats, expected, snaps)
    ids = _seed_specials(session, stats, expected, snaps)
    session.execute(insert(PlayerStatsWeekly), stats)
    session.execute(insert(PlayerExpectedPointsWeekly), expected)
    session.execute(insert(PlayerSnapCountsWeekly), snaps)
    rb1 = session.query(Player.id).filter(Player.full_name == "RB Player 01").scalar()
    rb2 = session.query(Player.id).filter(Player.full_name == "RB Player 02").scalar()
    ids.update(rb1=rb1, rb2=rb2)
    _seed_ngs(session, rb1, rb2, ids["chase"])
    session.add(ExpertRanking(player_id=ids["chase"], fantasypros_id="1", player_name="Ja'Marr Chase",
                              position="WR", team="CIN", source="nflverse_ffrankings", ranking_type="weekly",
                              scoring="ppr", season=CURRENT, week=7, rank=3, ecr=3.4, pos_rank="WR3"))
    session.commit()
    return ids


@pytest.fixture
def engine():
    test_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(test_engine)
    yield test_engine
    test_engine.dispose()


SECRET_ENV_VARS = ("GROQ_API_KEY", "FANTASYPROS_API_KEY", "ODDS_API_KEY", "YAHOO_CLIENT_ID", "YAHOO_CLIENT_SECRET",
                   "YAHOO_REDIRECT_URI", "TOKEN_ENCRYPTION_KEY", "AGENT_SHARE_PFR_DATA", "API_TOKEN",
                   "GROQ_MODEL", "LLM_CHAIN", "GEMINI_API_KEY", "GEMINI_MODEL", "OPENROUTER_API_KEY",
                   "OPENROUTER_MODEL", "OLLAMA_MODEL", "OLLAMA_BASE_URL")


def _clear_caches() -> None:
    from services import league_insights, matchup, player_index

    valuation_module._cache.clear()
    matchup._cache.clear()
    league_insights._cache.clear()
    league_insights._schedule.clear()
    player_index.clear_index_cache()
    from services import llm_client

    llm_client.clear_cooldowns()


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    """Fresh settings (no secrets) and an empty value cache for every test."""
    for name in SECRET_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    _clear_caches()
    yield
    get_settings.cache_clear()
    _clear_caches()


@pytest.fixture
def seeded(engine) -> dict[str, int]:
    with Session(engine) as session:
        return seed(session)


@pytest.fixture
def session(engine, seeded):
    with Session(engine) as db_session:
        yield db_session


@pytest.fixture
def client(engine, seeded, monkeypatch):
    from main import create_app
    import tools.rushleaders as rushleaders

    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override():
        with factory() as db_session:
            yield db_session

    monkeypatch.setattr(rushleaders, "engine", engine)
    app = create_app(warm_cache=False)
    app.dependency_overrides[get_session] = override
    with TestClient(app, base_url="http://localhost") as test_client:
        yield test_client
