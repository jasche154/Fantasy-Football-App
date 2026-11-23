"""Simulated Replay leagues over HTTP: the season in progress loads real only through its last finished week, a
new league freezes its own simulated season in its save, later real data never changes it, and its views
never show an unplayed week. The engine (services/sim_season, sim_world, sim_market) is a stand-in here; the
real engine runs end to end in test_replay_future_leagues.py."""
import json
from collections.abc import Iterator
from dataclasses import replace
from types import MappingProxyType, SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import services
from config import get_settings
from database.db import get_session
from database.models import Base, NflGame, PlayerStatsWeekly, ReplaySeasonData
from services import replay_blob, replay_live, replay_pool, sim_setup
from services.replay_pool import SeasonData
from tests import replay_fakes as fakes
from tests.test_replay_routes import _snapshot, get, post, refused

LIVE = 2026
FULL = fakes.fake_season(LIVE)                  # the season as it will really be (17 weeks)
THROUGH = 5                                     # the last finished week
LEAK_WEEK = THROUGH + 2
MARKET = 2.0                                    # the stand-in market's ADP stdev


def play_through(session: Session, through: int) -> None:
    """Final scores for every game through `through`: the weeks the live app has finished."""
    session.execute(update(NflGame).where(NflGame.season == LIVE, NflGame.week <= through)
                    .values(home_score=24, away_score=17))
    session.commit()


def fake_simulate(base: SeasonData, seed: int, from_week: int, prior=None) -> SeasonData:
    """Weeks from `from_week` on from FULL, real points shifted by the seed (each league its own future)."""
    def later(name: str) -> dict:
        return {key: value for key, value in getattr(FULL, name).items() if key[1] >= from_week}

    tables = {name: MappingProxyType({**getattr(base, name), **later(name)})
              for name in ("projected", "usage", "box", "injuries", "expert_ranks")}
    actual = {**base.actual, **{key: points + seed % 3 for key, points in later("actual").items()}}
    games = {pid: tuple(sorted({*base.team_by_week.get(pid, ()), *(g for g in played if g[0] >= from_week)}))
             for pid, played in FULL.team_by_week.items()}
    return replace(base, players=MappingProxyType({**FULL.players, **base.players}), actual=MappingProxyType(actual),
                   team_by_week=MappingProxyType(games), expert_age=FULL.expert_age,
                   notes=MappingProxyType({(fakes.BREAKOUT_ID, from_week): "Named the starter"}), **tables)


def fake_market(world: SeasonData) -> dict[int, tuple[float, float]]:
    """Today's ADP: by real points so far."""
    totals: dict[int, float] = {}
    for (pid, _), points in world.actual.items():
        totals[pid] = totals.get(pid, 0.0) + points
    ranked = sorted(totals, key=lambda pid: -totals[pid])
    return {pid: (float(pick), MARKET) for pick, pid in enumerate(ranked, start=1)}


@pytest.fixture(autouse=True)
def engine_stand_in(monkeypatch):
    monkeypatch.setattr(services, "sim_season", SimpleNamespace(simulate=fake_simulate), raising=False)
    monkeypatch.setattr(services, "sim_world", SimpleNamespace(estimate=lambda data, week, prior=None: data),
                        raising=False)
    monkeypatch.setattr(services, "sim_market", SimpleNamespace(adp=fake_market), raising=False)


def _app(url: str) -> Iterator[tuple[TestClient, Engine]]:
    engine = create_engine(url, connect_args={"check_same_thread": False},
                           **({"poolclass": StaticPool} if url == "sqlite://" else {}))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        fakes.seed_db(session, FULL)
        play_through(session, THROUGH)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override() -> Iterator[Session]:
        with factory() as session:
            yield session

    get_settings.cache_clear()
    from main import create_app

    app = create_app(warm_cache=False)
    app.dependency_overrides[get_session] = override
    with TestClient(app, base_url="http://localhost") as client:
        yield client, engine
    engine.dispose()


@pytest.fixture(scope="module")
def app() -> Iterator[tuple[TestClient, Engine]]:
    yield from _app("sqlite://")


@pytest.fixture
def client(app) -> TestClient:
    return app[0]


def _create(client: TestClient, **body) -> str:
    return "/games/%d" % post(client, "/games", {"season": LIVE, "seed": 4, "draft_slot": 3, **body})["game"]["id"]


def _frozen(engine: Engine, game: str) -> SeasonData:
    with Session(engine) as session:
        return replay_blob.load(session, int(game.rsplit("/", 1)[1]))


# ---------------- the season in progress ----------------

def test_the_live_season_is_the_newest_one_finished_through_its_last_complete_week(app):
    _, engine = app
    with Session(engine) as session:
        assert replay_live.current_live(session) == (LIVE, THROUGH)
        session.execute(update(NflGame).where(NflGame.season == LIVE, NflGame.week == THROUGH + 1,
                                              NflGame.home_team == FULL.schedule.games[-1].home_team)
                        .values(home_score=10, away_score=3))          # one game of the next week is final
        assert replay_live.current_live(session) == (LIVE, THROUGH)
        session.execute(update(NflGame).values(home_score=1, away_score=0))    # every game final
        assert replay_live.current_live(session) is None
        session.rollback()
        assert replay_live.current_live(session) == (LIVE, THROUGH) and replay_live.future_season(session) == LIVE + 1


def test_the_live_season_loads_real_only_through_its_last_finished_week(app):
    _, engine = app
    with Session(engine) as session:
        part = replay_pool.load_season_data(session, LIVE, "half", through=THROUGH)

    def upto(table) -> dict:
        return {key: value for key, value in table.items() if key[1] <= THROUGH}

    assert part.last_week == FULL.last_week and part.schedule.games == FULL.schedule.games
    for name in ("actual", "projected", "usage", "box", "injuries"):
        assert dict(getattr(part, name)) == upto(getattr(FULL, name)), name
    assert dict(part.expert_ranks) == upto(FULL.expert_ranks) and max(part.expert_age) == THROUGH
    assert all(week <= THROUGH for games in part.team_by_week.values() for week, _ in games)
    assert set(part.players) <= set(FULL.players) and part.notes == {}


# ---------------- creating a simulated league ----------------

def test_the_seasons_list_offers_the_live_and_the_future_season(client):
    rows = get(client, "/seasons")["simulated"]

    assert rows == [{"season": LIVE, "kind": "live", "through": THROUGH, "ready": True, "can_start_now": True,
                     "reason": None},
                    {"season": LIVE + 1, "kind": "future", "through": 0, "ready": True, "can_start_now": False,
                     "reason": None}]


def test_a_live_league_freezes_its_own_season_with_it(app):
    client, engine = app
    game = post(client, "/games", {"season": LIVE, "seed": 7})["game"]
    other = _create(client, seed=8)

    with Session(engine) as session:
        row = session.get(ReplaySeasonData, game["id"])
        assert (row.kind, row.known_through) == ("live", THROUGH)
    mine, theirs = _frozen(engine, f"/games/{game['id']}"), _frozen(engine, other)
    assert (game["kind"], game["sim_from"], game["start_week"], game["name"]) == ("live", THROUGH + 1, 1, f"{LIVE} Sim")
    assert mine is _frozen(engine, f"/games/{game['id']}")                      # one object per league
    assert mine.notes == {(fakes.BREAKOUT_ID, THROUGH + 1): "Named the starter"}
    week = THROUGH + 1
    assert [mine.actual[k] - theirs.actual[k] for k in mine.actual if k[1] == week][:3] == [-1, -1, -1]   # own future
    assert {k: v for k, v in mine.actual.items() if k[1] <= THROUGH} == {
        k: v for k, v in theirs.actual.items() if k[1] <= THROUGH}             # the same real past


def test_a_league_that_starts_now_drafts_on_todays_market(app):
    client, engine = app
    game = post(client, "/games", {"season": LIVE, "seed": 5, "start": "now"})["game"]

    shown, data = game["settings"], _frozen(engine, f"/games/{game['id']}")
    assert (game["kind"], game["sim_from"], game["start_week"]) == ("live", THROUGH + 1, THROUGH + 1)
    assert (shown["playoff_teams"], shown["regular_season_weeks"], game["end_week"]) == (4, 15, 17)
    assert THROUGH + 1 <= shown["trade_deadline_week"] <= 15
    market = fake_market(data.as_of(THROUGH + 1))
    assert {pid: (p.adp, p.adp_stdev) for pid, p in data.players.items() if p.adp is not None} == {
        pid: market[pid] for pid in data.players if pid in market}


def test_what_a_simulated_league_cannot_be(client, monkeypatch):
    assert refused(client, "/games", {"season": LIVE + 2})[0] == 422
    assert refused(client, "/games", {"season": 2021, "start": "now"})[1].startswith("Only the season in progress")
    monkeypatch.setattr(replay_live, "current_live", lambda session: (LIVE, 13))
    assert refused(client, "/games", {"season": LIVE, "start": "now"}) == (422, (
        "Only 2 regular-season weeks are left after week 13, and a league that starts now needs 3: pick a later "
        "championship week or fewer playoff teams."))
    assert refused(client, "/games", {"season": LIVE + 1, "start": "now"}) == (
        422, "Only the season in progress can start now: other leagues start with a preseason draft.")
    assert sim_setup.now_window(9, 17, 4) == (15, 13) and sim_setup.now_window(12, 17, 4) == (15, 14)


def test_a_simulated_league_is_drafted_and_played_to_a_champion(client):
    game = _create(client, seed=11, teams=8)
    post(client, game + "/draft/auto", whole_draft="true")

    while (summary := get(client, game))["phase"] != "complete":
        if summary["phase"] == "waivers":
            post(client, game + "/advance", {"step": "wednesday", "week": summary["week"]})
        post(client, game + "/advance", {"step": "play", "week": summary["week"]})

    assert summary["champion"] is not None and summary["kind"] == "live"
    assert len(get(client, game + "/scoreboard", week=THROUGH + 3)["results"]) == 4      # a simulated week, played


def test_listing_games_never_reads_a_season(app):
    client, engine = app
    _create(client)
    statements: list[str] = []
    listen = lambda *args: statements.append(args[2])        # noqa: E731 (before_cursor_execute's statement)
    event.listen(engine, "before_cursor_execute", listen)
    try:
        listed = get(client, "/games")["results"]
    finally:
        event.remove(engine, "before_cursor_execute", listen)

    assert listed and statements and not any("replay_season_data" in sql for sql in statements)


def test_deleting_a_simulated_league_deletes_its_season(app):
    client, engine = app
    game = _create(client)

    post(client, game + "/delete")

    with Session(engine) as session:
        assert session.get(ReplaySeasonData, int(game.rsplit("/", 1)[1])) is None
    assert refused(client, game, method="GET")[0] == 404


# ---------------- frozen: later real data never reaches a saved league ----------------

def test_new_real_data_reaches_new_leagues_only(tmp_path_factory):
    url = f"sqlite:///{(tmp_path_factory.mktemp('sim') / 'live.db').as_posix()}"
    for client, engine in _app(url):
        old = _create(client)
        before = replay_blob.dumps(_frozen(engine, old))
        star = next(pid for (pid, week) in FULL.actual if week == 1 and pid // 1000 == 11)       # a running back
        with Session(engine) as session:                         # a stat correction and the next week finished
            session.execute(update(PlayerStatsWeekly).where(PlayerStatsWeekly.player_id == star,
                                                            PlayerStatsWeekly.week == 1)
                            .values(rushing_yards=PlayerStatsWeekly.rushing_yards + 100))
            play_through(session, THROUGH + 1)
        replay_pool.clear_cache()                                # what a prepare run ends with
        after = _frozen(engine, old)
        new = post(client, "/games", {"season": LIVE, "seed": 4})["game"]
        fresh = _frozen(engine, f"/games/{new['id']}")
        get(client, old + "/players", status="all")              # the old league still plays

    assert replay_blob.dumps(after) == before and after.actual[(star, 1)] == FULL.actual[(star, 1)]
    assert new["sim_from"] == THROUGH + 2 and fresh.actual[(star, 1)] == pytest.approx(FULL.actual[(star, 1)] + 10)


# ---------------- no hindsight ----------------

def test_a_simulated_leagues_screens_are_identical_when_its_unplayed_weeks_differ(app):
    client, engine = app
    screens_never_leak(client, engine, _create(client, seed=32, draft_slot=5))


def screens_never_leak(client: TestClient, engine: Engine, game: str) -> None:
    """Drafted and played to LEAK_WEEK, the league's screens are the same on another future (and the week then
    played scores from the future it really has)."""
    post(client, game + "/draft/auto", whole_draft="true")
    for week in range(1, LEAK_WEEK):
        post(client, game + "/advance", {"step": "play", "week": week})
        post(client, game + "/advance", {"step": "wednesday", "week": week + 1})
    league = int(game.rsplit("/", 1)[1])
    data = _frozen(engine, game)
    shaken = fakes.future_changed(data, after_week=LEAK_WEEK)
    hidden = replace(fakes.patched(shaken, actual={k: v + 7 for k, v in data.actual.items() if k[1] == LEAK_WEEK}),
                     notes=MappingProxyType({**data.notes, (fakes.BUST_ID, LEAK_WEEK + 1): "Out for the year"}))

    mine = _snapshot(client, game)
    with Session(engine) as session:                             # the same league on another future
        session.get(ReplaySeasonData, league).blob = replay_blob.dumps(hidden)
        session.commit()
    replay_blob.clear_cache()
    theirs = _snapshot(client, game)

    assert len(mine) > 20 and [name for name in mine if json.dumps(mine[name]) != json.dumps(theirs[name])] == []
    post(client, game + "/advance", {"step": "play", "week": LEAK_WEEK})
    rows = get(client, game + "/team", week=LEAK_WEEK)["results"]
    scored = [row for row in rows if row["points"] is not None]
    assert scored and all(row["points"] == hidden.actual[(row["player"]["id"], LEAK_WEEK)] for row in scored)
