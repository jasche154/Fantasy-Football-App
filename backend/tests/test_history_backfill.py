"""Backfilling past weeks without hindsight."""
from types import SimpleNamespace

from sqlalchemy import select

from database.models import LearnedWeights, ProjectionHistory
from services import history_backfill as hb
from services import weight_learning as wl
from services.league import LeagueSettings
from services.valuation import build_valuation
from tests.test_weight_learning import _seed_history


def test_point_in_time_only_keeps_games_before_the_week(session, seeded):
    valuation = build_valuation(session, LeagueSettings())
    week = 3
    past = hb.point_in_time(valuation, week)
    assert all(g.week < week for p in past.seasons for g in p.games)
    assert any(g.week >= week for p in valuation.seasons for g in p.games)     # the full season had later games
    assert past.priors is valuation.priors


def test_record_week_keeps_rows_saved_live_before_kickoff(session, seeded):
    pid = next(v for v in seeded.values() if isinstance(v, int))
    session.add(ProjectionHistory(season=2026, week=2, scoring="ppr", player_id=pid, position="RB", projection=12.0,
                                  if_active=12.0, play_prob=1.0, floor=None, ceiling=None, sources='{"model": 12.0}'))
    session.commit()
    source = SimpleNamespace(points=30.0)
    player = SimpleNamespace(player_id=pid, position="RB", projection=30.0, if_active=30.0, play_prob=1.0,
                             floor=None, ceiling=None, sources={"model": source})
    assert hb.record_week(session, 2026, 2, "ppr", [player]) == 0
    row = session.scalar(select(ProjectionHistory).where(ProjectionHistory.week == 2))
    assert row.projection == 12.0


def test_learning_can_stop_at_a_week_so_backfill_learns_in_order(session, seeded):
    _seed_history(session, seeded, week=9)
    _seed_history(session, seeded, week=11)
    assert [u.week for u in wl.learn(session, 2026, through_week=9)] == [9]
    assert [u.week for u in wl.learn(session, 2026)] == [11]
    assert [r.week for r in session.scalars(select(LearnedWeights).order_by(LearnedWeights.week))] == [9, 11]


def test_weeks_argument():
    assert hb._weeks("1-6") == [1, 2, 3, 4, 5, 6]
    assert hb._weeks("4") == [4]
