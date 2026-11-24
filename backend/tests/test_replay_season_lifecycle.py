"""The season lifecycle needs no code edit when a season ends: walk the newest season from mid-season to complete,
then the next season's schedule arriving, then its week 1 going final (services/replay_live, sim_setup, replay_data,
replay_pool, GET /seasons and POST /games)."""
from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, insert, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from database.models import Base, NflGame, PlayerStatsWeekly, ReplayProjection, TeamGameStats
from services import replay_data, replay_live, replay_pool, sim_setup
from tests import replay_fakes as fakes
from tests.test_replay_routes import get, post
from tests.test_replay_sim_leagues import FULL, LIVE, THROUGH, _app, play_through

NEXT = LIVE + 1
LAST = FULL.last_week


def _on(monkeypatch, day: date) -> None:
    monkeypatch.setattr(replay_live, "today", lambda: day)


def _day_after(week: int, days: int = 1) -> date:
    return fakes.sunday(LIVE, week) + timedelta(days=days)


@pytest.fixture
def db(monkeypatch) -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    _on(monkeypatch, _day_after(THROUGH, 4))           # Thursday before week THROUGH + 1
    with Session(engine) as session:
        fakes.seed_db(session, FULL)
        play_through(session, THROUGH)
        yield session
    engine.dispose()
    replay_pool.clear_cache()


def _next_season(session: Session, week_one_final: bool) -> None:
    """NEXT's schedule (FULL's, a year on); week 1's games final with stats loaded, or none played."""
    following = fakes.fake_season(NEXT)
    games = fakes._game_rows(following)
    session.execute(insert(NflGame), [{**g, **({"home_score": 21, "away_score": 14}
                                               if week_one_final and g["week"] == 1 else {})} for g in games])
    if week_one_final:
        pid = next(iter(FULL.players))
        session.execute(insert(PlayerStatsWeekly), [{"player_id": pid, "season": NEXT, "week": 1, "season_type": "REG"}])
        session.execute(insert(TeamGameStats), [fakes._defense_row(following, 1, 3, "BUF", "MIA")])
    session.commit()


# ---------------- a cancelled game never freezes the season ----------------

def test_a_game_without_a_score_holds_its_week_back_only_while_it_can_still_be_played(db, monkeypatch):
    cancelled = next(g for g in FULL.schedule.games if g.week == 3)
    db.execute(update(NflGame).where(NflGame.season == LIVE, NflGame.week == 3,
                                     NflGame.home_team == cancelled.home_team).values(home_score=None, away_score=None))

    assert replay_live.current_live(db) == (LIVE, THROUGH)          # weeks ago: it was never played
    _on(monkeypatch, _day_after(3))                                 # the Monday after: it may still be
    assert replay_live.current_live(db) == (LIVE, 2)


def test_a_cancelled_game_in_the_last_week_does_not_keep_the_season_live(db, monkeypatch):
    play_through(db, LAST)
    db.execute(update(NflGame).where(NflGame.season == LIVE, NflGame.week == LAST,
                                     NflGame.home_team == FULL.schedule.games[-1].home_team)
               .values(home_score=None, away_score=None))
    _on(monkeypatch, _day_after(LAST))
    assert replay_live.current_live(db) == (LIVE, LAST - 1)

    _on(monkeypatch, _day_after(LAST, replay_live.STALE_DAYS + 1))
    assert replay_live.current_live(db) is None and replay_live.complete_season(db) == LIVE


# ---------------- each state of the year ----------------

def test_mid_season_the_newest_season_is_live_and_the_next_one_is_the_future(db):
    assert replay_live.current_live(db) == (LIVE, THROUGH)
    assert replay_live.replay_seasons(db) == replay_pool.SUPPORTED_SEASONS
    assert (replay_live.future_season(db), sim_setup.source(db)) == (NEXT, (LIVE, THROUGH))
    assert [sim_setup.kind_of(db, s) for s in (2025, LIVE, NEXT)] == [("replay", 0), ("live", THROUGH), ("future", 0)]
    assert replay_data._check_season(LIVE, db) == THROUGH
    with pytest.raises(ValueError):
        sim_setup.kind_of(db, NEXT + 1)


def test_once_the_season_is_complete_it_is_an_ordinary_replay_season(db, monkeypatch):
    play_through(db, LAST)
    _on(monkeypatch, _day_after(LAST))

    assert replay_live.current_live(db) is None
    assert replay_live.replay_seasons(db) == range(replay_pool.SUPPORTED_SEASONS[0], LIVE + 1)
    assert replay_live.future_season(db) == NEXT and sim_setup.source(db) == (LIVE, None)
    assert [sim_setup.kind_of(db, s) for s in (LIVE, NEXT)] == [("replay", 0), ("future", 0)]
    assert replay_data._check_season(LIVE, db) is None             # preparable as a whole season
    assert replay_pool.ready_last_weeks(db) == {LIVE: LAST}         # eligible for "random"
    assert sim_setup.future_problem(db, NEXT) is None


def test_a_complete_season_missing_its_last_weeks_projections_asks_for_a_prepare(db, monkeypatch):
    play_through(db, LAST)
    _on(monkeypatch, _day_after(LAST))
    db.execute(ReplayProjection.__table__.delete().where(ReplayProjection.week == LAST))   # a live prepare's limit

    assert replay_pool.ready_last_weeks(db) == {}
    assert sim_setup.future_problem(db, NEXT) == f"Prepare {LIVE} first: the {NEXT} season is built from it."


def test_with_next_seasons_schedule_loaded_and_nothing_played_it_is_still_the_future(db, monkeypatch):
    play_through(db, LAST)
    _on(monkeypatch, date(NEXT, 9, 1))
    _next_season(db, week_one_final=False)

    assert replay_live.current_live(db) is None and replay_live.complete_season(db) == LIVE
    assert replay_live.future_season(db) == NEXT and sim_setup.source(db) == (LIVE, None)
    assert [sim_setup.kind_of(db, s) for s in (LIVE, NEXT)] == [("replay", 0), ("future", 0)]
    with pytest.raises(ValueError, match="can't be prepared"):
        replay_data._check_season(NEXT, db)


def test_once_next_seasons_week_one_is_final_it_is_live_and_the_one_after_is_the_future(db, monkeypatch):
    play_through(db, LAST)
    _next_season(db, week_one_final=True)
    _on(monkeypatch, date(NEXT, 9, 16))

    assert replay_live.current_live(db) == (NEXT, 1)
    assert replay_live.replay_seasons(db) == range(replay_pool.SUPPORTED_SEASONS[0], LIVE + 1)
    assert replay_live.future_season(db) == NEXT + 1 and sim_setup.source(db) == (NEXT, 1)
    assert [sim_setup.kind_of(db, s) for s in (LIVE, NEXT, NEXT + 1)] == [
        ("replay", 0), ("live", 1), ("future", 0)]
    assert replay_data._check_season(NEXT, db) == 1 and replay_data._check_season(LIVE, db) is None
    assert sim_setup.future_problem(db, NEXT + 1) == f"Prepare {NEXT} first: the {NEXT + 1} season is built from it."


# ---------------- preparing the season once it is complete ----------------

def test_a_complete_season_is_prepared_whole_and_its_new_weeks_reload_once(db, monkeypatch):
    calls: list[tuple] = []

    def step(name: str, result: dict | None = None):
        return lambda *args, **kwargs: calls.append((name, args[1:], kwargs)) or dict(result or {})

    for name in ("load_game_results", "load_snaps", "load_expert_ranks", "load_projections", "load_injuries"):
        monkeypatch.setattr(replay_data, name, step(name))
    monkeypatch.setattr("services.replay_live_data.load_live_adp", step("adp", {"skipped": True}))
    monkeypatch.setattr(replay_data, "ensure_defenses", lambda session: 32)

    replay_data.prepare_season(db, LIVE)                           # live: weeks 1..THROUGH
    play_through(db, LAST)
    _on(monkeypatch, _day_after(LAST))
    calls.clear()
    replay_data.prepare_season(db, LIVE)                           # complete: the last weeks too
    first = dict((name, (args, kwargs)) for name, args, kwargs in calls)
    calls.clear()
    replay_data.prepare_season(db, LIVE)                           # nothing new
    again = dict((name, (args, kwargs)) for name, args, kwargs in calls)

    assert first["load_projections"][0][-1] is None                # through None: every week, the last included
    assert first["load_expert_ranks"][0][-1] and first["load_injuries"][0][-1]
    assert not again["load_expert_ranks"][0][-1] and not again["load_injuries"][0][-1]
    assert "adp" in first                                          # FFC may still have no list: MFL fills in


# ---------------- over HTTP ----------------

@pytest.fixture
def complete_app(monkeypatch, tmp_path):
    _on(monkeypatch, _day_after(LAST))
    for client, engine in _app(f"sqlite:///{(tmp_path / 'done.db').as_posix()}"):
        with Session(engine) as session:
            play_through(session, LAST)
        yield client
    replay_pool.clear_cache()


def test_a_finished_season_is_listed_and_played_like_any_past_season(complete_app):
    client = complete_app
    seasons = get(client, "/seasons")

    assert seasons["results"][-1]["season"] == LIVE and seasons["results"][-1]["ready"]
    assert [(row["season"], row["kind"], row["ready"]) for row in seasons["simulated"]] == [(NEXT, "future", True)]
    game = post(client, "/games", {"season": LIVE, "seed": 3})["game"]
    drawn = post(client, "/games", {"season": "random", "seed": 3})["game"]
    assert (game["kind"], game["sim_from"], game["name"]) == ("replay", None, f"{LIVE} Replay")
    assert (drawn["season"], drawn["kind"]) == (LIVE, "replay")


def test_a_badly_shaped_offseason_file_is_the_future_seasons_reason_not_a_500(complete_app, monkeypatch, tmp_path):
    from services import sim_offseason

    monkeypatch.setattr(sim_offseason, "DATA_DIR", tmp_path)
    sim_offseason.path(NEXT).write_text("[]", encoding="utf-8")

    future = get(complete_app, "/seasons")["simulated"][-1]
    assert (future["season"], future["ready"]) == (NEXT, False)
    assert future["reason"] == f"offseason_{NEXT}.json: the file must be a JSON object with 'rookies' and 'moves' lists."
