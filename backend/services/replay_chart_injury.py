"""Replay League trade chart: what an absence costs a player's chart (replay_chart._injured). Only the chart: a
player's availability and expected games missed (lineups, waivers) stay the real expected games.

`discount(missed, left)`: the share of his healthy chart an absence of `missed` more games (this week's
included) out of his team's `left` remaining games takes off:
- the user's targets (the user's choice), interpolated linearly between DISCOUNT_ANCHORS: out 1-4 weeks about
  5-12% off, 6+ weeks 35-45%, falling back each week as the return nears;
- never less than SHARE_SLOPE x (share of his remaining games he misses - SHARE_START), a ramp that matters
  once he misses most of what is left (about 55% on): out 2 of the last 3 games 19%, 4 of the last 5 27%, 5 of
  the last 6 29%, while one game missed stays 6% however late (he loses one of several games). The ramp is as
  steep as the user's targets allow: one more game missed, or one more game left, never moves the discount by
  more than 15 points, and for a fixed return week it never rises from one week to the next;
- all of the remaining games (or a return after the league's last week): the whole chart. An "out for the season" report (replay_news) may be wrong (its
  noise reads some long absences as season-ending first), so it keeps SEASON_REPORT_KEEP of the chart by the
  games left: what such reports were really worth, the user's discount of the games missed when he came back
  and 0 when he did not, over every IR or PUP absence 2018-25 and 20 news draws each
  (`python -m services.replay_chart_eval --season-reports`): 0.3% with 0-3 games left, 1.8% with 4-7, 6.5% with
  8-11, 16.7% with 12+ (he came back 0.3%, 2.3%, 9.5% and 27% of the time).
The chart discounts the value he had before the absence (replay_values._frozen), so it falls once by this
curve and climbs back as the return nears; the experts' and the model's ranks, which drop while he is out,
are not read until he is available for a game again (replay_values._absences).
At 13 games left: 1 missed 6%, 2 8%, 3 10%, 4 12%, 6 40%, 8 45%, 10 45%, 12 45%.
Real results (services.replay_chart_eval, 2018-25, regular lineup) say a known absence costs at least its share
of games and players come back worse (out players realised 0.40 of their pre-injury value; the chart keeps 0.63),
so the user's targets price injured players above what they realised (held-out RMSE of out players 3.185 ->
3.516 with the frozen value).

`unreported_discount`: an out player without a news report (no hard tag streak in replay_news) loses the
expected discount over NO_REPORT_ODDS, how many games such absences really missed (counting this week, each
truncated at the games left, never back = all of them), at least the discount of missing IR_OUT_SHARE of the
rest when tagged IR or PUP (the user's long-absence cap, 45%, whatever the games left). Measured with `python -m services.replay_chart_eval --absences` (2018-25, weeks
1-10, every skill player out with no hard tag: 3,253 player-weeks): 16% played that week after all, 23%
missed one game, 13% two, 19% never came back that season. With 13 games left that costs about 30%; with one
left 84% (never all: some play after all). A plain O tag's first week, for comparison: median 2 games, mean
3.0, 7% never back (604 player-weeks); it always has a news report, so a star newly out on an O tag reported
back in a week or two loses 6-8%.

Pure: no database.
"""
from bisect import bisect_right

DISCOUNT_ANCHORS = ((0.0, 0.0), (1.0, 0.06), (4.0, 0.12), (6.0, 0.40), (8.0, 0.45), (12.0, 0.45))
SHARE_SLOPE, SHARE_START = 0.6, 0.35  # the floor ramp (module docstring)
# (team games missed counting this week, None = never back that season) -> share of unreported absences
NO_REPORT_ODDS = ((0, 0.1565), (1, 0.2321), (2, 0.1303), (3, 0.0812), (4, 0.0559), (5, 0.0418), (6, 0.0277),
                  (7, 0.0264), (8, 0.0209), (9, 0.0157), (10, 0.0101), (11, 0.0052), (12, 0.0043), (13, 0.0028),
                  (14, 0.0012), (15, 0.0003), (None, 0.1875))
IR_OUT_SHARE = 0.80            # tagged IR or PUP: at least this share of the remaining games (replay_values)
# chart kept on an "out for the season" report, by the games left // 4 (0-3, 4-7, 8-11, 12+): measured (module docstring)
SEASON_REPORT_KEEP = (0.003, 0.018, 0.065, 0.167)


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
    return max(_anchored(missed), SHARE_SLOPE * (missed / left - SHARE_START))


def season_report_discount(left: int) -> float:
    """The discount on an "out for the season" report with `left` games to go (module docstring)."""
    return 1.0 - SEASON_REPORT_KEEP[min(max(left, 0) // 4, len(SEASON_REPORT_KEEP) - 1)] if left > 0 else 1.0


def unreported_discount(games_left: int, ir_tag: bool) -> float:
    """The expected discount of an out player with no news report (module docstring)."""
    total = sum(odds for _, odds in NO_REPORT_ODDS)
    expected = sum(odds * discount(games_left if missed is None else min(missed, games_left), games_left)
                   for missed, odds in NO_REPORT_ODDS) / total
    return max(expected, DISCOUNT_ANCHORS[-1][1]) if ir_tag else expected     # IR: a long absence, any length
