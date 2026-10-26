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


def test_season_ending_injury_leaves_no_rest_of_season_value(session, seeded):
    torn, sprain = _pid(session, "RB Player 01"), _pid(session, "RB Player 02")
    session.add(PlayerStatus(player_id=torn, injury_status="IR", injury_body_part="Knee - ACL"))
    session.add(PlayerStatus(player_id=sprain, injury_status="Questionable", injury_body_part="Ankle"))
    session.commit()
    shares = ros_consensus.status_shares(session, 10)
    assert shares[torn] == 0.0 and shares[sprain] == 1.0
    assert ros_consensus.availability(session, 10) == {torn: 0.0}            # only players expected to miss
    valuation_module._cache.clear()
    hurt = build_valuation(session, LeagueSettings()).by_id[torn]
    assert hurt.ros_ppg == 0.0 and hurt.trade_value == 0.0


def _ros_row(session, pid, week, scoring, rank):
    session.add(WeeklySourceProjection(season=2026, week=week, source="yahoo_ros", scoring=scoring, expert="consensus",
                                       player_id=pid, player_name="x", position="RB", rank=rank))


def test_expert_ranks_survive_a_half_published_week_and_missing_format(session, seeded):
    a, b, c = _pid(session, "RB Player 01"), _pid(session, "RB Player 02"), _pid(session, "RB Player 03")
    _ros_row(session, a, 7, "ppr", 5.0)
    _ros_row(session, b, 7, "ppr", 9.0)
    _ros_row(session, a, 8, "ppr", 4.0)              # week 8 so far: only one PPR row ...
    _ros_row(session, c, 8, "half", 12.0)            # ... and a player ranked only in half-PPR
    _ros_row(session, c, 8, "std", 13.0)
    session.commit()
    assert ros_consensus.expert_ranks(session, 2026, "ppr") == {a: 4.0, b: 9.0, c: 12.0}
    assert ros_consensus.expert_ranks(session, 2026, "std")[c] == 13.0
