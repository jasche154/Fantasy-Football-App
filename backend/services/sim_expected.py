"""Simulated seasons: the analytic mean line of every player in a game (sim_game.expected, re-exported there).

The same model as sim_game.play's draw at its means: each side's means from the centred sides (sim_centre), the
skill players' lines from them, the kicker's field goals (at the drawn mean tries) and extra-point tries (one per
offensive or return TD that does not go for two, as the draw kicks them), the D/ST's takeaways and points allowed;
in a two-team game the closing window's and overtime's mean events are added on top (sim_closing_means), as the
draw plays them.
"""
from collections.abc import Collection, Mapping
from typing import TYPE_CHECKING

import numpy as np

from services.replay_box import BoxLine
from services.scoring import ScoringSettings
from services.sim_game import (
    PA_GRID, Expected, _dst_points, _fga_drawn, _kick_points, _mean_points, _pa_points, _side, _Side, _side_means,
    _side_of, _skill_points, _takeaways,
)
from services.sim_world import World, params

if TYPE_CHECKING:
    from services.sim_closing_means import Ending


def _tries(tds: float, against: dict | None) -> float:
    """A side's mean extra-point tries: its offensive and return TDs (the return TDs from the takeaways it makes
    against `against`, the opposing offense's means) less the share that go for two."""
    kick, dst = params()["kick"], params()["dst"]
    _, ints, fumbles = _takeaways(against)
    returns = ints * dst["int_td"] + fumbles * dst["fum_td"] + dst["st_td"]
    return (tds + returns) * (1 - kick["two_point"])


def expected(world: World, home: str, away: str | None, out: Collection[int], scoring: ScoringSettings,
             exits: Mapping[int, float] | None = None, target: Mapping[str, float] | None = None,
             shift: Mapping[str, float] | None = None, factors: Mapping[str, float] | None = None,
             week: int | None = None, versus: str | None = None) -> dict[int, Expected]:
    """Mean line and points of every active player of `home` (and of `away` when given) in their game.
    `away` None: `home` alone (only home's players are returned), against a league-average opponent, or against
    `versus`'s pass and run defense with no game played against it (no centring, no window). `target`:
    {team: mean points} the game is centred on (see the module docstring); `shift`: {team: points} added to
    its own mean instead; `factors`: {team: factor} to apply as given (sim_centre.factor)."""
    from services.sim_centre import centred
    exits = exits or {}
    teams = [home] if away is None else [home, away]
    opponent = {home: away if away is not None else versus, away: home}
    built = {team: _side(world, team, opponent[team], out, exits) if exits else _side_of(world, team, opponent[team], out)
             for team in teams}
    from services import sim_closing_means as closing_means
    sides = centred(built, target, shift, factors, week)
    means = {team: _side_means(side) for team, side in sides.items()}
    dst = params()["dst"]
    league = params()["league"]
    other = {home: away, away: home}
    before = {team: _mean_points(m, means.get(other[team])) for team, m in means.items()}
    ends = {}                                        # the draw adds the closing window and overtime (sim_closing_means)
    if away is not None and not factors:
        ends = dict(zip((home, away), closing_means.endings(before[home], before[away])))
    team_points = {team: before[team] + (ends[team].window + ends[team].overtime if team in ends else 0.0)
                   for team in means}
    out_lines: dict[int, Expected] = {}
    for team, side in sides.items():
        m, opp = means[team], (away if team == home else home)
        out_lines.update(_expected_players(side, m, scoring, ends.get(team)))
        if side.kicker is not None:
            tds = float(m["rec_td"].sum() + m["rush_td"].sum())
            yards = float(m["rec_yds"].sum() + m["rush_yds"].sum())
            kicks = _kick_points(_fga_drawn(yards, tds, side.fg_scale), _tries(tds, means.get(opp)))
            out_lines[side.kicker] = Expected(BoxLine(), round(
                kicks + (closing_means.kicker_points(ends[team], params()["kick"]["bands"]) if team in ends else 0.0), 2))
        if side.dst is not None:
            allowed = team_points.get(opp, league["points"])
            sacks, ints, fumbles = _takeaways(means.get(opp))
            tds = ints * dst["int_td"] + fumbles * dst["fum_td"] + dst["st_td"]
            allowed = min(max(allowed, 0.0), float(PA_GRID[-1]))
            grid = np.exp(-0.5 * ((PA_GRID - allowed) / league["points_sd"]) ** 2)
            pa = float(grid @ _pa_points() / grid.sum())
            points = _dst_points(sacks, ints, fumbles, tds, dst["safety"], dst["block"], 1000) - _dst_points(
                0, 0, 0, 0, 0, 0, 1000) + pa + (closing_means.defense_points(ends[team]) if team in ends else 0.0)
            out_lines[side.dst] = Expected(BoxLine(), round(points, 2))
    return out_lines


def _expected_players(side: _Side, m: dict, scoring: ScoringSettings,
                      end: "Ending | None" = None) -> dict[int, Expected]:
    """Every skill player's mean line and points; `end`: with the closing window's and overtime's mean events
    (a sim_closing_means.Ending), None without."""
    lines: dict[int, dict[str, float]] = {}
    for i, pid in enumerate(side.receivers[:-1]):
        lines[pid] = {"targets": m["targets"][i], "rec": m["catches"][i], "rec_yds": m["rec_yds"][i],
                      "rec_td": m["rec_td"][i], "fumbles": m["catches"][i] * side.rec_fumble[i]}
    for i, pid in enumerate(side.rushers[:-1]):
        line = lines.setdefault(pid, {})
        line.update(carries=m["carries"][i], rush_yds=m["rush_yds"][i], rush_td=m["rush_td"][i],
                    fumbles=line.get("fumbles", 0.0) + (m["carries"][i] - m["kneels"][i]) * side.fumble[i])
    if side.qb is not None:      # the sacks' lost fumbles, split as the draw splits them by the share each played
        sacked = m["sacks"] * params()["league"]["sack_fumble"]
        line = lines.setdefault(side.qb, {})
        line.update(pass_yds=float(m["rec_yds"].sum()), pass_td=float(m["rec_td"].sum()), pass_int=m["ints"],
                    fumbles=line.get("fumbles", 0.0) + sacked * side.qb_share)
        if side.backup is not None and side.qb_share < 1.0:
            line = lines.setdefault(side.backup, {})
            line["fumbles"] = line.get("fumbles", 0.0) + sacked * (1.0 - side.qb_share)
    if end is not None:
        from services.sim_closing_means import credit_means
        credit_means(lines, side, m, end)
    out = {}
    for pid, line in lines.items():
        fumbles = line.pop("fumbles", 0.0)
        box = BoxLine(**{key: float(value) for key, value in line.items()})
        out[pid] = Expected(box, round(_skill_points(box, fumbles, side.positions[pid], scoring), 2))
    return out
