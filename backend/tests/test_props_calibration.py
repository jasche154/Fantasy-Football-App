"""Grading stored prop-derived stats against box scores and the point-in-time per-stat correction."""
import json

import pytest

from database.models import Player, PlayerStatsWeekly, WeeklySourceProjection
from services import props_calibration as pc
from services import weekly_rankings
from services.league import LeagueSettings
from services.weekly_rankings import build_weekly_rankings
from tests.test_weekly_rankings import seed_week

SEASON = 2030             # clear of the seeded seasons


def _seed_props(session, weeks=(1, 2, 3), players=40, rush_actual=72.0, rush_line=60.0):
    """RBs whose props expect rush_line yards and who run for rush_actual; one scratch per week."""
    pids = []
    for i in range(players + 1):
        player = Player(full_name=f"Prop RB {i}", position="RB", nfl_team="AAA", gsis_id=f"00-prop-{i}")
        session.add(player)
        session.flush()
        pids.append(player.id)
    for week in weeks:
        for i, pid in enumerate(pids):
            session.add(WeeklySourceProjection(season=SEASON, week=week, source=pc.SOURCE, player_id=pid,
                                               player_name=f"Prop RB {i}", position="RB", team="AAA",
                                               stats=json.dumps({"rush_yds": rush_line, "scrimmage_tds": 0.5})))
            if i < players:                                  # the last one never plays: void props
                session.add(PlayerStatsWeekly(player_id=pid, season=SEASON, week=week, season_type="REG",
                                              rushing_yards=rush_actual, rushing_tds=i % 2, receiving_tds=0))
    session.commit()
    return pids


def test_pairs_skip_void_props_and_respect_the_week(session, seeded):
    _seed_props(session)
    assert len(pc.load_pairs(session, SEASON)) == 3 * 40
    before_three = pc.load_pairs(session, SEASON, before_week=3)
    assert {p.week for p in before_three} == {1, 2} and len(before_three) == 80
    assert pc.load_pairs(session, SEASON, before_week=1) == []
    pair = before_three[0]
    assert pair.actual["rush_yds"] == 72.0 and pair.props == {"rush_yds": 60.0, "scrimmage_tds": 0.5}


def test_fit_shrinks_toward_one_and_needs_enough_samples(session, seeded):
    _seed_props(session)
    pairs = pc.load_pairs(session, SEASON)
    factors = pc.fit(pairs)
    n = len(pairs)
    expected = 1 + n / (n + pc.PSEUDO_SAMPLES) * (72.0 / 60.0 - 1)
    assert factors["rush_yds"] == pytest.approx(expected, abs=1e-4) and 1.0 < factors["rush_yds"] < 1.2
    assert factors["scrimmage_tds"] == pytest.approx(1.0, abs=1e-4)     # 0.5 expected, 0.5 scored on average
    assert pc.fit(pairs[: pc.MIN_SAMPLES - 1])["rush_yds"] == 1.0


def test_fit_caps_the_factor():
    pairs = [pc.Pair(SEASON, 1, i, "RB", {"rush_yds": 10.0}, {"rush_yds": 100.0}) for i in range(2000)]
    assert pc.fit(pairs)["rush_yds"] == pc.MAX_FACTOR


def test_corrected_scales_only_stats_with_a_factor():
    assert pc.corrected({"rush_yds": 50.0, "rec": 3.0}, {"rush_yds": 1.1}) == {"rush_yds": pytest.approx(55.0),
                                                                                "rec": 3.0}


def test_evaluate_reports_rmse_with_and_without_the_correction(session, seeded):
    _seed_props(session)
    report = pc.evaluate(pc.load_pairs(session, SEASON), LeagueSettings())
    rolling = report["rolling"]
    assert [w["week"] for w in rolling["weeks"]] == [2, 3]                # week 1 has nothing to learn from
    assert rolling["rmse_corrected"] < rolling["rmse_raw"]                 # the props were 12 yards short
    assert report["bias"]["rush_yds"] == {"n": 120, "mean_miss": 12.0}
    assert report["leave_one_week_out"]["n"] == 120


def test_factors_apply_only_when_enabled(session, seeded, monkeypatch):
    _seed_props(session)
    assert pc.factors_for(session, SEASON, 3) == {}
    monkeypatch.setattr(pc, "ENABLED", True)
    assert pc.factors_for(session, SEASON, 3)["rush_yds"] > 1.0


def test_weekly_rankings_apply_the_correction_to_props_points(session, seeded, monkeypatch):
    ids = seed_week(session)
    league = LeagueSettings()
    plain = {p.player_id: p for p in build_weekly_rankings(session, league).players}[ids["wr1"]]
    monkeypatch.setattr(pc, "factors_for", lambda *_: {"rec_yds": 1.1})
    weekly_rankings._cache.clear()
    monkeypatch.setattr(weekly_rankings.rankings_store, "load", lambda *_: None)
    fixed = {p.player_id: p for p in build_weekly_rankings(session, league).players}[ids["wr1"]]
    # 95 props receiving yards -> 104.5: +0.95 points at 0.1 per yard.
    assert fixed.sources["vegas_props"].points == pytest.approx(plain.sources["vegas_props"].points + 0.95, abs=0.01)
    assert fixed.sources["espn"].points == plain.sources["espn"].points
