"""Replay League trade chart: what an absence costs a player's chart (replay_chart._injured). Only the chart: a
player's availability and expected games missed (lineups, waivers) stay the real expected games.

`discount(missed, left)`: the share of his healthy chart an absence of `missed` more games (this week's
included) out of his team's `left` remaining games takes off:
- the user's targets (the user's choice), interpolated linearly between DISCOUNT_ANCHORS: out 1-4 weeks about
  5-12% off, 6+ weeks 35-45%, falling back each week as the return nears;
- never less than (share of his remaining games he misses) ^ SHARE_POWER, so a late-season absence still
  costs (out 2 of the last 3 games: 48%) and a near-season-long one heads toward zero;
- all of the remaining games (out for the season): the whole chart.
At 13 games left: 1 missed 6%, 2 8%, 3 10%, 4 12%, 6 40%, 8 45%, 10 62%.
Real results (services.replay_chart_eval, 2018-25) say a known absence costs about its share of games (out
players realised 0.49 of a healthy player's value; the old share x 0.9 chart kept 0.49). The user's targets
keep more (0.63 on average), so within them the deep end is used: the anchors' upper bounds and the lowest
SHARE_POWER (1.8) that keeps every 1-4-week absence within 12% at 13 games left (held-out RMSE of out
players 3.20 with the middle of the ranges and a square, 3.17 at the deep end, 2.94 with the old chart).

`unreported_missed`: an out player without a news report (no hard tag streak in replay_news) is expected to
miss NO_REPORT_MISSED more games, at least IR_OUT_SHARE of the rest when tagged IR or PUP. Measured on
2018-25 (weeks 1-10, every skill player out with no hard tag: 3,253 player-weeks): 3.3-3.8 more games
counting this one at every streak length (median 2). A plain O tag's first week, for comparison: median 2,
mean 3.0, 7% never back that season (604 player-weeks), so a star newly out on an O tag reported back in a
week or two loses 7-10%.

Pure: no database.
"""
from bisect import bisect_right

DISCOUNT_ANCHORS = ((0.0, 0.0), (1.0, 0.06), (4.0, 0.12), (6.0, 0.40), (8.0, 0.45), (12.0, 0.45))
SHARE_POWER = 1.8
NO_REPORT_MISSED = 3.5         # more games an out player without a report misses (measured, see the docstring)
IR_OUT_SHARE = 0.80            # tagged IR or PUP: at least this share of the remaining games (replay_values)
LONG_DISCOUNT = DISCOUNT_ANCHORS[-1][1]     # the most a long but not season-long absence takes off by the anchors


def _anchored(missed: float) -> float:
    weeks = [w for w, _ in DISCOUNT_ANCHORS]
    if missed >= weeks[-1]:
        return DISCOUNT_ANCHORS[-1][1]
    k = bisect_right(weeks, missed)
    (w0, d0), (w1, d1) = DISCOUNT_ANCHORS[k - 1], DISCOUNT_ANCHORS[k]
    return d0 + (missed - w0) / (w1 - w0) * (d1 - d0)


def discount(missed: float, left: float) -> float:
    """0-1: the share of his healthy chart lost to missing `missed` of his team's `left` remaining games."""
    if missed <= 0 or left <= 0:
        return 0.0
    if missed >= left:
        return 1.0
    return max(_anchored(missed), (missed / left) ** SHARE_POWER)


def unreported_missed(games_left: int, ir_tag: bool) -> float:
    """Games an out player with no news report is expected to miss (module docstring)."""
    missed = min(float(games_left), NO_REPORT_MISSED)
    return max(missed, IR_OUT_SHARE * games_left) if ir_tag else missed
