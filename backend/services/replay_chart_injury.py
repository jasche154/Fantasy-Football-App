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

`unreported_discount`: an out player without a news report (no hard tag streak in replay_news) loses the
expected discount over NO_REPORT_ODDS, how many games such absences really missed (counting this week, each
truncated at the games left, never back = all of them), at least the discount of missing IR_OUT_SHARE of the
rest when tagged IR or PUP. Measured with `python -m services.replay_chart_eval --absences` (2018-25, weeks
1-10, every skill player out with no hard tag: 3,253 player-weeks): 16% played that week after all, 23%
missed one game, 13% two, 19% never came back that season. With 13 games left that costs about 30%; with one
left 84% (never all: some play after all). A plain O tag's first week, for comparison: median 2 games, mean
3.0, 7% never back (604 player-weeks); it always has a news report, so a star newly out on an O tag reported
back in a week or two loses 6-8%.

Pure: no database.
"""
from bisect import bisect_right

DISCOUNT_ANCHORS = ((0.0, 0.0), (1.0, 0.06), (4.0, 0.12), (6.0, 0.40), (8.0, 0.45), (12.0, 0.45))
SHARE_POWER = 1.8
# (team games missed counting this week, None = never back that season) -> share of unreported absences
NO_REPORT_ODDS = ((0, 0.1565), (1, 0.2321), (2, 0.1303), (3, 0.0812), (4, 0.0559), (5, 0.0418), (6, 0.0277),
                  (7, 0.0264), (8, 0.0209), (9, 0.0157), (10, 0.0101), (11, 0.0052), (12, 0.0043), (13, 0.0028),
                  (14, 0.0012), (15, 0.0003), (None, 0.1875))
IR_OUT_SHARE = 0.80            # tagged IR or PUP: at least this share of the remaining games (replay_values)


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


def unreported_discount(games_left: int, ir_tag: bool) -> float:
    """The expected discount of an out player with no news report (module docstring)."""
    total = sum(odds for _, odds in NO_REPORT_ODDS)
    expected = sum(odds * discount(games_left if missed is None else min(missed, games_left), games_left)
                   for missed, odds in NO_REPORT_ODDS) / total
    return max(expected, discount(IR_OUT_SHARE * games_left, games_left)) if ir_tag else expected
