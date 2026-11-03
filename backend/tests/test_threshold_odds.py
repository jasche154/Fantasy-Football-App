"""'N+ points' odds: consistent with the score ranges, in the weekly rows, and the calibration report."""
import numpy as np
import pytest
from sqlalchemy.orm import Session

from database.models import NflGame, Player, PlayerStatsWeekly, ProjectionHistory
from services import score_ranges, threshold_calibration
from services import valuation as valuation_module
from services.league import LeagueSettings
from services.threshold_odds import THRESHOLDS, chance_at_least, threshold_odds
from services.weekly_rankings import build_weekly_rankings
from tests.test_weekly_rankings import seed_week

Q = (0.0, 2.0, 5.0, 9.0, 13.0, 18.0, 21.0)          # at score_ranges.LEVELS


def test_chance_matches_the_quantiles_it_passes_through():
    assert chance_at_least(Q, 9.0) == pytest.approx(0.5)        # the median
    assert chance_at_least(Q, 18.0) == pytest.approx(0.10)      # the ceiling (90th percentile)
    assert chance_at_least(Q, 15.5) == pytest.approx(0.175)     # halfway between the 75th and 90th
    assert chance_at_least(Q, 0.0) == 1.0
    assert chance_at_least(Q, 100.0) == 0.0
    assert 0.0 < chance_at_least(Q, 25.0) < 0.05                # the tail past the 95th


def test_chance_agrees_with_the_matchup_simulations_draws():
    draws = score_ranges.sample(Q, np.random.default_rng(3).random(200_000))
    for threshold in (4.0, 10.0, 16.0, 22.0):
        assert chance_at_least(Q, threshold) == pytest.approx(float(np.mean(draws >= threshold)), abs=0.005)


def test_zero_heavy_quantiles_and_monotone_thresholds():
    low = (0.0, 0.0, 0.0, 0.0, 1.41, 3.22, 4.42)                # score_ranges.LOW_TIER for a 1-point player
    assert chance_at_least(low, 0.5) == pytest.approx(0.5 - 0.25 * 0.5 / 1.41, abs=1e-6)
    odds = threshold_odds("WR", Q, 1.0)
    assert [t for t, _ in odds] == list(THRESHOLDS["WR"])
    assert [p for _, p in odds] == sorted((p for _, p in odds), reverse=True)


def test_odds_include_the_chance_he_sits_and_skip_unmodeled_players():
    full = dict(threshold_odds("RB", Q, 1.0))
    questionable = dict(threshold_odds("RB", Q, 0.6))
    assert questionable[10.0] == pytest.approx(0.6 * full[10.0], abs=0.001)
    assert threshold_odds("K", Q, 1.0) == () and threshold_odds("WR", None, 1.0) == ()


def test_weekly_rows_carry_odds_consistent_with_their_range(session, seeded):
    ids = seed_week(session)
    wr1 = next(p for p in build_weekly_rankings(session, LeagueSettings()).players if p.player_id == ids["wr1"])
    assert [t for t, _ in wr1.threshold_odds] == list(THRESHOLDS["WR"])
    assert wr1.threshold_odds == threshold_odds("WR", wr1.quantiles, wr1.play_prob)
    assert wr1.play_prob == 1.0 and wr1.quantiles is not None
    # His ceiling (90th percentile) is a 10% shot.
    assert chance_at_least(wr1.quantiles, wr1.quantiles[score_ranges.CEILING_INDEX]) == pytest.approx(0.10)


def test_weekly_endpoint_serializes_the_odds(client, engine):
    with Session(engine) as db_session:
        seed_week(db_session)
    valuation_module._cache.clear()
    first = client.get("/api/rankings/weekly", params={"position": "WR", "limit": 1}).json()["results"][0]
    assert [o["points"] for o in first["threshold_odds"]] == list(THRESHOLDS["WR"])
    assert all(0.0 <= o["probability"] <= 1.0 for o in first["threshold_odds"])


def test_calibration_uses_completed_weeks_and_counts_a_no_show_as_zero(session, seeded):
    star = Player(full_name="Star WR", position="WR", nfl_team="AAA", gsis_id="00-star")
    benched = Player(full_name="Scratched WR", position="WR", nfl_team="AAA", gsis_id="00-scratched")
    session.add_all([star, benched])
    session.flush()
    for week, final in ((2, True), (9, False)):
        session.add(NflGame(game_id=f"c{week}", season=2026, week=week, game_type="REG", home_team="AAA",
                            away_team="OPP", home_score=21 if final else None, away_score=17 if final else None))
    for week in (2, 9):
        session.add(PlayerStatsWeekly(player_id=star.id, season=2026, week=week, season_type="REG",
                                      fantasy_points=40.0, receptions=0))
    out = Player(full_name="Ruled-out WR", position="WR", nfl_team="AAA", gsis_id="00-out")
    session.add(out)
    session.flush()
    for pid, week, play in ((star.id, 2, 1.0), (benched.id, 2, 1.0), (star.id, 9, 1.0), (out.id, 2, 0.0)):
        session.add(ProjectionHistory(season=2026, week=week, scoring="ppr", player_id=pid, position="WR",
                                      projection=14.0 * play, if_active=14.0, play_prob=play, floor=None,
                                      ceiling=None, sources="{}"))
    session.commit()
    ruled_out = threshold_calibration.predictions(session, 2026, "ppr", ruled_out=True)
    assert [(p, hit) for _, _, p, hit in ruled_out] == [(0.0, False)] * 3   # kept out of the scores below
    complete = threshold_calibration.completed_weeks(session, 2026)
    assert 2 in complete and 9 not in complete                   # week 9 isn't final yet
    rows = threshold_calibration.predictions(session, 2026, "ppr")
    assert len(rows) == 2 * len(THRESHOLDS["WR"])
    hits = [hit for _, _, _, hit in rows]
    assert hits.count(True) == 3 and hits.count(False) == 3      # 40 points clears all; the scratch is 0
    report = threshold_calibration.report(rows)
    assert report["overall"]["n"] == 6 and report["by_threshold"]["low"]["hit_rate"] == 0.5


def test_calibration_refuses_the_seasons_the_ranges_were_fit_on(monkeypatch, tmp_path):
    ranges = tmp_path / "score_ranges.json"
    ranges.write_text('{"seasons": "2021-2025", "positions": {}}', encoding="utf-8")
    monkeypatch.setattr(score_ranges, "PATH", ranges)
    assert threshold_calibration.fit_seasons() == {2021, 2022, 2023, 2024, 2025}
    with pytest.raises(SystemExit):
        threshold_calibration.main(["--season", "2024"])


def test_calibration_report_bins_and_brier():
    rows = [("WR", 0, 0.05, False)] * 9 + [("WR", 0, 0.05, True)] + [("WR", 1, 0.75, True)] * 3 + [("WR", 1, 0.75, False)]
    report = threshold_calibration.report(rows)
    bins = {b["bin"]: b for b in report["overall"]["bins"]}
    assert bins["0.0-0.1"]["actual"] == 0.1 and bins["0.7-0.8"]["actual"] == 0.75
    assert report["by_threshold"]["high"] == {"n": 0}
