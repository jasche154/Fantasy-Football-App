"""Simulated seasons: centring a game on a prediction (sim_game.expected and sim_game.play).

Given each side's wanted mean points (a `target`, or its own mean + a `shift`), the side's TD rates per catch
and per carry are multiplied by one factor f, its yards by f ** yards_power (the fitted slope of log yards on log
points in real team games, sim_lines.json) and, when f < 1, its field-goal tries by f ** `game.fg_power` too: a weak offense
reaches field-goal range less often, so low totals are reachable (only return TDs and safeties, about 0.7 points
a game, stay as they are); above 1 the tries stay as fga_mean gives them (a strong offense's extra drives end in
TDs; scaling them up too gave a 38-point side 4.3 tries, far above real ones).

The factor is solved on the closed form of sim_game._mean_points (`_points_at`): TDs = f T0, yards = f^p Y0,
field-goal tries = min(1, f) ** fg_power x fga_mean(yards, TDs) (capped where the draw caps them). Newton steps on that scalar
function need no side rebuilt; one check on the scaled side's real means then corrects for the only thing the
closed form leaves out (a TD rate reaching sim_game.MAX_TD_RATE).

The wanted points include overtime and the closing window (a real line's total does): a two-team game is first
centred on the wanted points less the overtime points a side scores on average (`ot_points`: sim_draw's overtime
model at the measured simulated regulation tie rate), yards included; then its TD rates and field-goal tries alone
are scaled down by the window's mean points (sim_closing.window_points; the factor's yards power 0) and its yards by
the window TDs' mean yards (sim_closing.window_yards, credited to the scorers in the window), so the draw before the
cut plus the window averages the wanted points (`_without_window`). A two-team game given no target is centred on
its own mean points (a shift of 0), so the window and overtime never add to it. In a two-team game each side's
targets and carries first follow its expected margin (`_scripted`, sim_closing.line_script: real favourites run
more), before its factor is solved, so the means the draw and the projections use already have it.
"""
import math
from collections.abc import Mapping
from dataclasses import replace

import numpy as np

from services.sim_world import params

FACTOR_RANGE = (0.02, 8.0)       # a side's factor stays in this range
NEWTON_STEPS = 25                # on the closed form (microseconds each); stops at CLOSED_TOLERANCE
CLOSED_TOLERANCE = 1e-3          # points, on the closed form
TARGET_TOLERANCE = 0.05          # points, on the real means (else one correction)
MIN_TARGET = 1.0                 # a side is never centred below this many points
FG_POWER = 1.0                   # default of the tuned `game.fg_power`
REGULATION_TIES = 0.042          # share of simulated games tied after regulation (measured over 2,272 games with the closing window)
# re-measure REGULATION_TIES whenever `game.td_drives` or sim_closing.json changes: they set how often regulation ends tied


def ot_points() -> float:
    """The mean overtime points a side scores: ties x the chance overtime is decided x the winning score / 2."""
    from services import sim_draw
    field_goal = sim_draw.ot_field_goal()
    return REGULATION_TIES * (1 - sim_draw.ot_tie()) * (3 * field_goal + 6 * (1 - field_goal)) / 2


def fg_power() -> float:
    """How field-goal tries follow a factor below 1 (`game.fg_power`)."""
    return float(params()["game"].get("fg_power", FG_POWER))


def scaled(side, factor: float, power: float):
    """`side` with TD rates x factor, field-goal tries x min(1, factor) ** fg_power and yards x factor ** power."""
    yards = factor ** power
    return replace(side, ypc=side.ypc * yards, rec_td=side.rec_td * factor, ypr=side.ypr * yards,
                   rush_td=side.rush_td * factor, fg_scale=side.fg_scale * min(1.0, factor) ** fg_power())


def _points_at(f: float, tds: float, yards: float, rest: float, fg_scale: float, power: float) -> tuple[float, float]:
    """(points, d points / d f) at factor f for a side with (TDs, yards, field-goal scale) at f = 1; `rest`: the
    return TDs' and safeties' points, which the factor leaves alone."""
    from services.sim_game import TWO_POINT_SUCCESS, fga_mean
    kick = params()["kick"]
    per_td = 6 + (1 - kick["two_point"]) * kick["xp"] + kick["two_point"] * 2 * TWO_POINT_SUCCESS
    make = float(np.dot(kick["bands"], kick["make"]))
    cap = float(round(kick["fga_chances"]))
    below = fg_power() if f < 1.0 else 0.0
    fga = min(1.0, f) ** fg_power() * fg_scale * fga_mean(yards * f ** power, tds * f)
    slope_fga = 0.0 if fga >= cap else fga * (below + kick["fga_yards"] * power + kick["fga_tds"] * tds * f) / f
    return per_td * tds * f + 3 * make * min(fga, cap) + rest, per_td * tds + 3 * make * slope_fga


def _solve(tds: float, yards: float, rest: float, fg_scale: float, power: float, target: float, start: float = 1.0) -> float:
    low, high = FACTOR_RANGE
    f = start
    for _ in range(NEWTON_STEPS):
        points, slope = _points_at(f, tds, yards, rest, fg_scale, power)
        if abs(points - target) < CLOSED_TOLERANCE or slope <= 0:
            break
        f = min(high, max(low, f + (target - points) / slope))
    return f


def factor(side, against: dict | None, target: float, m: dict | None = None, power: float | None = None) -> float:
    """The factor that makes `side`'s mean points (sim_game._mean_points against `against`) `target`; `m`: its
    means when already worked out; `power`: how yards follow the factor (default sim_lines.yards_power)."""
    from services.sim_game import MAX_TD_RATE, _mean_points, _side_means
    from services.sim_lines import yards_power
    power, m = yards_power() if power is None else power, m if m is not None else _side_means(side)
    tds = float(m["rec_td"].sum() + m["rush_td"].sum())
    yards = float(m["rec_yds"].sum() + m["rush_yds"].sum())
    closed, _ = _points_at(1.0, tds, yards, 0.0, side.fg_scale, power)
    rest = _mean_points(m, against) - closed            # return TDs and safeties: untouched by the factor
    f = _solve(tds, yards, rest, side.fg_scale, power, target)
    if max(side.rec_td.max(), side.rush_td.max()) * f <= MAX_TD_RATE:
        return f                                         # the closed form is exact
    got = _mean_points(_side_means(scaled(side, f, power)), against)
    if abs(got - target) >= TARGET_TOLERANCE:          # a TD rate hit the cap: aim past it by the miss
        f = _solve(tds, yards, rest, side.fg_scale, power, 2 * target - got, f)
    return f


def team_factor(world, team: str, out, shift: float) -> float:
    """The factor that moves `team`'s mean points against a league-average opponent by `shift` (sim_market.values:
    one per team, reused for every set of players out)."""
    from services.sim_game import _mean_points, _side_means, _side_of
    side = _side_of(world, team, None, out)
    m = _side_means(side)
    return factor(side, None, max(MIN_TARGET, _mean_points(m, None) + shift), m)


def _without_window(side, against: dict | None, wanted: float, lead: float):
    """A two-team game's side (already centred on `wanted` before the window) with the closing window's mean points
    and TD yards at its expected lead at the cut (`lead`, sim_closing.cut_lead) taken out of its draw (see the
    module docstring)."""
    from services import sim_closing
    from services.sim_game import _side_means
    m = _side_means(side)
    yards = float(m["rec_yds"].sum() + m["rush_yds"].sum())
    keep = max(0.0, 1.0 - sim_closing.window_yards(lead) / yards) if yards > 0 else 1.0
    side = replace(side, ypc=side.ypc * keep, ypr=side.ypr * keep)
    regulation = max(MIN_TARGET, wanted - sim_closing.window_points(lead))
    return scaled(side, factor(side, against, regulation, power=0.0), 0.0)


def _scripted(side, margin: float):
    """`side` with its targets and carries moved by its expected margin (sim_closing.line_script: a favourite runs
    more and throws a little less, as real ones do against the Vegas line)."""
    from services import sim_closing
    script = sim_closing.line_script()
    return replace(side, targets=side.targets * math.exp(script["targets"] * margin),
                   carries=side.carries * math.exp(script["carries"] * margin))


def _wanted(sides: dict, means: dict, against: dict, target, shift, factors) -> dict[str, float]:
    """{team: the points its draw before overtime is centred on} for the teams given a target or a shift."""
    from services.sim_game import _mean_points
    overtime = ot_points() if len(sides) == 2 else 0.0
    wanted = {}
    for team in sides:
        if factors and team in factors:
            continue
        goal = (target or {}).get(team)
        if goal is None and team in (shift or {}):
            goal = _mean_points(means[team], against.get(team)) + shift[team]       # the truth minus the belief: OT in both
        if goal is not None:
            wanted[team] = max(MIN_TARGET, goal) - overtime
    return wanted


def _in_week(side, week: int):
    """`side` with week `week`'s pass / run mix (sim_calendar: its targets, carries and yards per catch)."""
    from services import sim_calendar
    targets, carries, ypt = sim_calendar.factors(week)
    return replace(side, targets=side.targets * targets, carries=side.carries * carries, ypc=side.ypc * ypt)


def centred(sides: dict, target: Mapping[str, float] | None = None, shift: Mapping[str, float] | None = None,
            factors: Mapping[str, float] | None = None, week: int | None = None) -> dict:
    """`sides` with each one's factor applied so its mean points (overtime and the closing window included) are
    target[team], or its own + shift[team] (kept at MIN_TARGET or more), or with factors[team] as given; a team in
    none stays as it is, except in a two-team game given nothing, centred on its own mean points. `week`: the game's
    week, whose pass / run mix it plays with (sim_calendar; None: the season's average)."""
    if len(sides) == 2 and not target and not shift and not factors:
        shift = {team: 0.0 for team in sides}
    if not target and not shift and not factors:
        return sides
    if week is not None:
        sides = {team: _in_week(side, week) for team, side in sides.items()}
    from services.sim_closing import cut_lead
    from services.sim_game import _side_means
    from services.sim_lines import yards_power
    power = yards_power()
    means = {team: _side_means(side) for team, side in sides.items()}
    teams = list(sides)
    against = {teams[0]: means[teams[-1]], teams[-1]: means[teams[0]]} if len(teams) == 2 else {}
    wanted = _wanted(sides, means, against, target, shift, factors)
    out = {}
    for team, side in sides.items():
        other = next((t for t in teams if t != team), None)
        if factors and team in factors:
            out[team] = scaled(side, factors[team], power)
        elif team not in wanted:
            out[team] = side
        else:
            if other in wanted:
                side = _scripted(side, wanted[team] - wanted[other])
                side = scaled(side, factor(side, against.get(team), wanted[team]), power)
                out[team] = _without_window(side, against.get(team), wanted[team], cut_lead(wanted[team], wanted[other]))
            else:
                out[team] = scaled(side, factor(side, against.get(team), wanted[team], means[team]), power)
    return out
