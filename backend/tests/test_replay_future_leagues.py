"""Simulated Replay leagues with the REAL engine (services/sim_season, sim_world, sim_market, sim_next_year) over a
seeded database: the future season is built from what is known today (the season in progress through its last
finished week, or the finished season), with the offseason's rookies, retirements and moves, and both a future
and a live league play to a champion through the routes."""
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from database.models import NflGame
from services import replay_pool, replay_state as rs, sim_next_year, sim_offseason, sim_setup
from services.replay_state import Settings
from services.sim_offseason import Move, Rookie
from tests import replay_fakes as fakes
from tests.test_replay_routes import get, post
from tests.test_replay_sim_leagues import (  # noqa: F401 (live_today is an autouse fixture)
    FULL, LIVE, THROUGH, _app, _frozen, live_today, screens_never_leak,
)

FUTURE = LIVE + 1
RETIRED = fakes.player_id("WR", 2)
MOVED = fakes.player_id("RB", 2)
ROOKIES = (Rookie("Fake Phenom", "WR", "MIA", 1, "Ohio State"), Rookie("Fake Passer", "QB", "PIT", 3, "Texas"),
           Rookie("Fake Back", "RB", "DAL", 70, "Utah"))


def _moved_to() -> str:
    return "MIA" if FULL.team_at(MOVED, THROUGH + 1) != "MIA" else "NYJ"


@pytest.fixture(autouse=True)
def offseason(monkeypatch):
    """A small offseason file: three rookies, a retirement, a move, a name nobody has."""
    moves = (Move(FULL.players[RETIRED].name, "WR", None, "Retires"),
             Move(FULL.players[MOVED].name, "RB", _moved_to(), "Signs elsewhere"),
             Move("Nobody Here", "TE", None, "Not in the pool"))
    monkeypatch.setattr(sim_offseason, "load", lambda season: (ROOKIES, moves) if season == FUTURE else ((), ()))


@pytest.fixture(scope="module")
def app() -> Iterator[tuple[TestClient, Engine]]:
    yield from _app("sqlite://")


def _settings() -> Settings:
    return Settings(season=FUTURE, sim_from=1)


def _draftable(base, position: str) -> int:
    return sum(1 for pid, p in base.players.items()
               if p.position == position and (p.adp is not None or (pid, 1) in base.projected))


def _check_future(base, belief, last_week: int) -> None:
    rookies = sim_next_year.rookie_ids(FUTURE, ROOKIES)
    assert base.season == FUTURE and not base.actual and not base.team_by_week and base.last_week == last_week
    assert RETIRED not in base.players and base.players[MOVED].adp_team == _moved_to()
    assert set(rookies) <= set(base.players) and base.players[min(rookies)].name == "Fake Phenom"
    assert _draftable(base, "K") >= replay_pool.MIN_KICKERS and _draftable(base, "DST") >= replay_pool.MIN_DEFENSES
    assert {p.team for p in belief.players.values() if p.position == "QB" and p.starts == 1.0} == set(belief.teams)


def test_the_future_season_is_built_from_the_season_in_progress(app):
    _, engine = app
    with Session(engine) as session:
        assert sim_setup.source(session) == (LIVE, THROUGH)
        base, belief = sim_setup._future_base(session, FUTURE, _settings())
    _check_future(base, belief, 18)
    assert len(base.schedule.games) == 272                       # generated: no 2027 schedule is stored
    assert {g.week for g in base.schedule.games} == set(range(1, 19))


def test_a_stored_schedule_is_used_and_a_finished_season_still_builds(app):
    _, engine = app
    with Session(engine) as session:
        session.execute(insert(NflGame), [{"game_id": f"{FUTURE}_{g.week:02d}_{g.away_team}_{g.home_team}",
                                           "season": FUTURE, "week": g.week, "game_type": "REG", "home_team": g.home_team,
                                           "away_team": g.away_team, "gameday": g.gameday} for g in FULL.schedule.games])
        session.execute(update(NflGame).where(NflGame.season == LIVE).values(home_score=20, away_score=17))
        try:
            assert sim_setup.source(session) == (LIVE, None)
            assert sim_setup.future_problem(session, FUTURE) is None
            base, belief = sim_setup._future_base(session, FUTURE, _settings())
        finally:
            session.rollback()
            replay_pool.clear_cache()
    _check_future(base, belief, FULL.last_week)
    assert {(g.week, g.home_team, g.away_team) for g in base.schedule.games} == {
        (g.week, g.home_team, g.away_team) for g in FULL.schedule.games}


def _play_out(client: TestClient, game: str) -> dict:
    post(client, game + "/draft/auto", whole_draft="true")
    while (summary := get(client, game))["phase"] != "complete":
        if summary["phase"] == "waivers":
            post(client, game + "/advance", {"step": "wednesday", "week": summary["week"]})
        post(client, game + "/advance", {"step": "play", "week": summary["week"]})
    return summary


def test_a_future_league_is_drafted_and_played_to_a_champion(app):
    client, engine = app
    future = next(row for row in get(client, "/seasons")["simulated"] if row["kind"] == "future")
    assert (future["season"], future["ready"], future["reason"]) == (FUTURE, True, None)
    game = post(client, "/games", {"season": FUTURE, "seed": 3, "teams": 8})["game"]
    assert (game["kind"], game["sim_from"], game["start_week"], game["name"]) == ("future", 1, 1, f"{FUTURE} Sim")

    summary = _play_out(client, f"/games/{game['id']}")

    data = _frozen(engine, f"/games/{game['id']}")
    assert summary["champion"] is not None and data.season == FUTURE
    assert all((pid, week) in data.actual for pid, games in data.team_by_week.items() for week, _ in games)
    assert any(pid >= sim_next_year.ROOKIE_ID_BASE for pid, _ in data.actual)          # rookies play


def test_a_live_league_is_played_to_a_champion_on_the_real_engine(app):
    client, engine = app
    game = post(client, "/games", {"season": LIVE, "seed": 9, "teams": 8})["game"]

    summary = _play_out(client, f"/games/{game['id']}")

    data = _frozen(engine, f"/games/{game['id']}")
    assert summary["champion"] is not None and summary["kind"] == "live"
    assert {k: v for k, v in data.actual.items() if k[1] <= THROUGH} == {
        k: v for k, v in FULL.actual.items() if k[1] <= THROUGH and k[0] in data.players}
    assert any(week > THROUGH for _, week in data.actual)


def test_a_live_league_that_starts_now_drafts_on_the_real_engines_market(app):
    client, engine = app
    game = post(client, "/games", {"season": LIVE, "seed": 6, "teams": 8, "start": "now"})["game"]
    path = f"/games/{game['id']}"

    data = _frozen(engine, path)
    market = {pid: p.adp for pid, p in data.players.items() if p.adp is not None}
    with Session(engine) as session:
        settings = rs.load(session, game["id"])[1]
    assert (game["start_week"], data.adp_format) == (THROUGH + 1, settings.adp_format)
    assert len(market) >= 8 * 15 and sum(market[pid] != p.adp for pid, p in FULL.players.items()
                                         if pid in market and p.adp is not None) > len(market) // 2
    post(client, path + "/draft/auto", whole_draft="true")
    post(client, path + "/advance", {"step": "play", "week": THROUGH + 1})
    assert get(client, path)["week"] == THROUGH + 2


def test_a_future_leagues_screens_never_show_an_unplayed_week(app):
    client, engine = app
    game = post(client, "/games", {"season": FUTURE, "seed": 32, "draft_slot": 5})["game"]
    screens_never_leak(client, engine, f"/games/{game['id']}")
