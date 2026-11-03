"""Chance a player scores at least N fantasy points this week, from the same range model as his floor/ceiling.

    P(points >= N) = play_prob * P(points >= N | he plays)

The "if he plays" part reads the empirical quantiles from services/score_ranges.py
(5th...95th percentile of actual / projected points, by position and projection tier, with
the touchdown-dependence and target-depth shape) as a piecewise-linear distribution: the
same one score_ranges.sample draws from in the matchup simulation, tails included
(score_ranges.knots). So a player whose 90th percentile (his ceiling) is 20.0 has a 10%
chance of 20+ if he plays, and the odds, the range and the simulation never disagree.

Thresholds are per position (THRESHOLDS); kickers and defenses have none.
Calibration on past weeks: python -m services.threshold_calibration --season 2026.
"""
from collections.abc import Sequence

from services.score_ranges import knots

THRESHOLDS: dict[str, tuple[float, ...]] = {
    "QB": (15.0, 20.0, 25.0),
    "RB": (10.0, 15.0, 20.0),
    "WR": (10.0, 15.0, 20.0),
    "TE": (8.0, 12.0, 16.0),
}


def chance_at_least(quantiles: Sequence[float], threshold: float) -> float:
    """P(points >= threshold | he plays) under the quantile distribution (point masses at ties included)."""
    levels, points = knots(quantiles)
    if threshold <= points[0]:
        return 1.0
    if threshold > points[-1]:
        return 0.0
    for i in range(len(points) - 1):
        low, high = points[i], points[i + 1]
        if low < threshold <= high:
            # The smallest cumulative level whose score reaches the threshold.
            reached = levels[i] + (levels[i + 1] - levels[i]) * (threshold - low) / (high - low)
            return 1.0 - reached
    return 0.0


def threshold_odds(position: str, quantiles: Sequence[float] | None,
                   play_prob: float) -> tuple[tuple[float, float], ...]:
    """((threshold, probability), ...) for the position's thresholds; () without a range or thresholds."""
    thresholds = THRESHOLDS.get(position)
    if not thresholds or not quantiles:
        return ()
    play = min(max(play_prob, 0.0), 1.0)
    return tuple((t, round(play * chance_at_least(quantiles, t), 3)) for t in thresholds)
