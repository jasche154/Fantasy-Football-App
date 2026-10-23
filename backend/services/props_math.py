"""Turn sportsbook player props into expected stats.

- Over/under lines come with a price on each side. Removing the bookmaker's margin
  ("de-vigging") gives the market's probability of the over.
- Count stats (receptions, pass TDs, interceptions) are treated as Poisson: we solve for
  the mean whose chance of beating the line equals that probability.
- Yardage is treated as roughly normal around the line, nudged by how far the over
  probability is from 50%. Lines are medians; yardage is right-skewed, so this slightly
  understates the mean, which is the conservative direction.
- Anytime-TD prices give P(at least one TD); for a Poisson count, mean = -ln(1 - p).
"""
import math
from statistics import NormalDist

POISSON_SEARCH_MAX = 30.0
POISSON_TOLERANCE = 1e-6
MAX_PROBABILITY = 0.995
# Typical per-side margin on a yes-only anytime TD price, removed when no "No" price is listed.
ANYTIME_TD_MARGIN = 0.07
# Standard deviation as a share of the line, from historical prop outcomes (rounded).
YARDAGE_SD_RATIO = {"pass_yds": 0.25, "rush_yds": 0.45, "rec_yds": 0.55}


def implied_probability(american: float) -> float:
    """-150 -> 0.6, +150 -> 0.4 (includes the bookmaker's margin)."""
    if american < 0:
        return -american / (-american + 100.0)
    return 100.0 / (american + 100.0)


def fair_probability(over_price: float, under_price: float) -> float:
    """Over probability with the margin removed (the two sides scaled to sum to 1)."""
    over, under = implied_probability(over_price), implied_probability(under_price)
    return over / (over + under)


def _clamp(probability: float) -> float:
    return min(max(probability, 1 - MAX_PROBABILITY), MAX_PROBABILITY)


def poisson_over(line: float, mean: float) -> float:
    """P(X > line) for a Poisson count with the given mean."""
    threshold = math.floor(line) + 1          # 4.5 -> need 5+; 4 -> need 5+ (push excluded)
    cumulative, term = 0.0, math.exp(-mean)
    for k in range(threshold):
        cumulative += term
        term *= mean / (k + 1)
    return 1.0 - cumulative


def poisson_mean(line: float, over_probability: float) -> float:
    """The Poisson mean whose P(X > line) equals over_probability (bisection)."""
    target = _clamp(over_probability)
    low, high = 0.0, POISSON_SEARCH_MAX
    while high - low > POISSON_TOLERANCE:
        middle = (low + high) / 2
        if poisson_over(line, middle) < target:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def yardage_mean(stat: str, line: float, over_probability: float) -> float:
    sd = YARDAGE_SD_RATIO[stat] * line
    return max(0.0, line + sd * NormalDist().inv_cdf(_clamp(over_probability)))


def anytime_td_mean(yes_price: float, no_price: float | None = None) -> float:
    if no_price is not None:
        probability = fair_probability(yes_price, no_price)
    else:
        probability = implied_probability(yes_price) / (1 + ANYTIME_TD_MARGIN)
    return -math.log(1 - _clamp(probability))
