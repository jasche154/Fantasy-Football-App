"""Local rankings snapshots, projection history, and scoring projections against results."""
import json

import pytest
from sqlalchemy import func, select

from database.models import ProjectionHistory, RankingsSnapshot
from services import projection_eval, rankings_store
from services import weekly_rankings as weekly
from services.league import LeagueSettings
from tests.test_injury_model import SEASON, WEEK, _seed_week


def test_first_build_is_saved_and_reused_after_a_restart(session, seeded, monkeypatch):
    _seed_week(session)
    weekly._cache.clear()
    first = weekly.build_weekly_rankings(session, LeagueSettings())
    assert session.scalar(select(func.count()).select_from(RankingsSnapshot)) == 1

    weekly._cache.clear()                                   # a restart empties memory
    monkeypatch.setattr(weekly, "_build", lambda *a, **k: pytest.fail("should load the saved snapshot"))
    again = weekly.build_weekly_rankings(session, LeagueSettings())
    assert [(p.player_id, p.projection) for p in again.players] == [(p.player_id, p.projection) for p in first.players]


def test_a_different_league_or_model_gets_its_own_snapshot():
    league = LeagueSettings()
    key = rankings_store.cache_key(league, WEEK, ("v1",))
    assert key != rankings_store.cache_key(LeagueSettings(scoring="half"), WEEK, ("v1",))
    assert key != rankings_store.cache_key(league, WEEK, ("v2",))
    assert key != rankings_store.cache_key(league, WEEK + 1, ("v1",))


def test_unreadable_snapshot_is_ignored(session, seeded):
    session.add(RankingsSnapshot(cache_key="bad", season=SEASON, week=WEEK, payload=b"not a pickle"))
    session.commit()
    assert rankings_store.load(session, "bad") is None


def test_history_records_pre_kickoff_projections(session, seeded):
    _seed_week(session)
    weekly._cache.clear()
    rankings = weekly.build_weekly_rankings(session, LeagueSettings())
    rows = session.scalars(select(ProjectionHistory)).all()
    assert len(rows) == len(rankings.players) and all(r.week == WEEK and r.scoring == "ppr" for r in rows)
    sample = next(r for r in rows if json.loads(r.sources))
    assert set(json.loads(sample.sources)) <= set(weekly.WEIGHTS)


def _row(pid, week, if_active, sources, position="RB"):
    return ProjectionHistory(season=SEASON, week=week, scoring="ppr", player_id=pid, position=position,
                             projection=if_active, if_active=if_active, play_prob=1.0, floor=None, ceiling=None,
                             sources=json.dumps(sources))


def test_evaluate_scores_blend_and_sources_on_played_games():
    rows = [_row(1, 7, 12.0, {"model": 10.0, "vegas_props": 13.0}),
            _row(2, 7, 8.0, {"model": 9.0}),
            _row(3, 7, 15.0, {"model": 15.0})]                       # no box score: didn't play
    report = projection_eval.evaluate(rows, {(1, 7): 14.0, (2, 7): 6.0})
    assert report["n"] == 2 and report["blend_mae"] == 2.0 and report["weeks"] == [7]
    assert report["sources"]["model"] == {"n": 2, "mae": 3.5, "blend_mae_same_games": 2.0}
    assert report["sources"]["vegas_props"]["mae"] == 1.0
    assert report["suggested_weights"] == {}                         # too few rows to suggest


def test_suggested_weights_favor_the_more_accurate_source():
    rows = [_row(i, 7, 10.0, {"model": 10.0 + 4, "vegas_props": 10.0 + 1}) for i in range(40)]
    weights = projection_eval.evaluate(rows, {(i, 7): 10.0 for i in range(40)})["suggested_weights"]
    assert weights["vegas_props"] > weights["model"] and sum(weights.values()) == pytest.approx(1.0, abs=0.01)
