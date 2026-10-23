"""Rest-of-season consensus blend."""
import json

import pytest

from database.models import Player, PlayerStatus, WeeklySourceProjection
from services import ros_consensus
from services import valuation as valuation_module
from services.league import LeagueSettings
from services.ros_consensus import RosInputs
from services.valuation import build_valuation


def test_blend_reweights_and_scales_by_availability():
    assert RosInputs(model=20.0).blended() == 20.0
    assert RosInputs(model=20.0, experts=10.0, weekly=10.0).blended() == pytest.approx(0.45 * 20 + 0.55 * 10)
    assert RosInputs(model=20.0, experts=10.0).blended() == pytest.approx((0.45 * 20 + 0.35 * 10) / 0.8, abs=0.01)
    assert RosInputs(model=20.0, availability=0.5).blended() == 10.0


def _pid(session, name):
    return session.query(Player.id).filter(Player.full_name == name).scalar()


def test_valuation_blends_expert_ros_ranks_weekly_points_and_injuries(session, seeded):
    top, low, hurt = _pid(session, "RB Player 01"), _pid(session, "RB Player 20"), _pid(session, "RB Player 02")
    for pid, rank in ((top, 30.0), (low, 1.0)):            # experts disagree with the model
        for expert in ("consensus", "7666"):
            session.add(WeeklySourceProjection(season=2026, week=7, source="yahoo_ros", scoring="ppr", expert=expert,
                                               player_id=pid, player_name="x", position="RB", rank=rank))
    session.add(WeeklySourceProjection(season=2026, week=7, source="espn", player_id=low, player_name="x",
                                       position="RB", stats=json.dumps({"rush_yds": 100.0, "rush_tds": 1.0})))
    session.add(PlayerStatus(player_id=hurt, injury_status="IR"))
    session.commit()
    valuation_module._cache.clear()
    by_id = build_valuation(session, LeagueSettings()).by_id
    assert by_id[low].expert_ros_rank == 1.0 and by_id[low].weekly_ppg == 16.0
    assert by_id[low].ros_ppg > by_id[low].model_ros_ppg            # experts and this week lift him
    assert by_id[top].ros_ppg < by_id[top].model_ros_ppg            # experts rank him RB30
    remaining = build_valuation(session, LeagueSettings()).weeks_remaining
    assert by_id[hurt].availability == pytest.approx((remaining - ros_consensus.IR_WEEKS) / remaining, abs=0.001)
    assert by_id[hurt].ros_ppg == pytest.approx(by_id[hurt].model_ros_ppg * by_id[hurt].availability, abs=0.02)


def test_rankings_endpoint_exposes_ros_sources(client):
    row = client.get("/api/rankings", params={"limit": 1}).json()["results"][0]
    assert set(row["ros_sources"]) == {"model", "experts", "expert_rank", "weekly", "availability"}
