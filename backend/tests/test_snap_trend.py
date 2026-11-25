"""Snap-share trend: a player whose recent snap share runs above his season's gets a slightly higher rate."""
import pytest

from services.injuries import Availability
from services.projections import GameLine, PlayerSeason
from services.weekly_adjust import Adjusted, explain
from services.weekly_model import USAGE_TREND_WEIGHT, Matchup, snap_trend, weekly_rate


def line(week, snap, points=10.0):
    return GameLine(week=week, season_type="REG", opponent="OPP", fantasy_points=points, expected_points=points,
                    expected_tds=None, passing_yards=0, passing_tds=0, interceptions=0, carries=5,
                    rushing_yards=20, rushing_tds=0, targets=3, receptions=2, receiving_yards=20,
                    receiving_tds=0, target_share=None, snap_pct=snap)


def player(snaps):
    return PlayerSeason(1, "P", "WR", "AAA", 2026, tuple(line(i + 1, s) for i, s in enumerate(snaps)))


def test_snap_trend_compares_last_three_games_to_the_season():
    assert snap_trend(player([50, 50, 50, 80, 80, 80])) == pytest.approx(80 / 65 - 1)
    assert snap_trend(player([90, 90, 90, 60, 60, 60])) == pytest.approx(60 / 75 - 1)
    assert snap_trend(player([70, 70, 70, 70, 70])) == 0.0


def test_snap_trend_needs_five_games_and_snap_data():
    assert snap_trend(player([20, 30, 90, 90])) == 0.0                     # too few games
    assert snap_trend(player([None] * 6)) == 0.0                           # no snap counts loaded
    assert snap_trend(player([50, 50, 50, None, None, None])) == 0.0       # nothing recent to compare
    assert snap_trend(player([None, 40, 40, 80, None, 80])) == pytest.approx(80 / 60 - 1)


def test_snap_trend_is_capped():
    assert snap_trend(player([1, 1, 1, 1, 1, 90, 90, 90])) == 1.0


def test_weekly_rate_scales_with_the_snap_trend():
    rising, flat = player([50, 50, 50, 80, 80, 80]), player([65] * 6)
    assert weekly_rate(flat, None) == pytest.approx(10.0)
    assert weekly_rate(rising, None) == pytest.approx(10.0 * (1 + USAGE_TREND_WEIGHT * (80 / 65 - 1)), abs=0.01)
    assert weekly_rate(rising, None, usage_weight=0.0) == pytest.approx(10.0)
    # The prior is shrunk in first; the trend scales the whole rate.
    assert weekly_rate(rising, 20.0) == pytest.approx((0.6 * 10 + 0.4 * 20) * (1 + USAGE_TREND_WEIGHT * (80 / 65 - 1)),
                                                      abs=0.01)


def _steps(trend):
    matchup = Matchup(opponent="BBB", home=True, sos_factor=1.0, sos_rank=None, implied_total=None, team_ppg=None,
                      game_final=False)
    adjusted = Adjusted(if_active=10.0, projection=10.0, floor=None, ceiling=None, boost_points=0.0, scenarios=(),
                        availability=Availability(1, 1.0, None, None, None, "none"), quantiles=None)
    return explain("P", "WR", 10.0, None, matchup, {}, {}, {}, 10.0, 1.0, adjusted, (), snap_trend=trend)


def test_explanation_mentions_a_notable_snap_trend():
    recent = _steps(0.3)[0]
    assert "snap share" in recent.detail and f"{USAGE_TREND_WEIGHT * 30:+.0f}%" in recent.detail
    assert "snap share" not in _steps(0.02)[0].detail
