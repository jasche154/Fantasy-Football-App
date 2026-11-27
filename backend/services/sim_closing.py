"""Simulated seasons: the closing minutes of a game, played from the score (sim_draw), and overtime's measured rates.

sim_draw draws a game up to the last `window` seconds of regulation (600: ten minutes, chosen with sim_eval; 300
left margins of 3 at 11.0% against a real 14.6%, and 900 only added noise). From there the ending is a real one:
the margin at the cut picks the set of real games that stood at that margin (sim_closing.json's `table`, fitted
from 2018-2025 play-by-play by sim_closing_fit), and one of them is drawn, its trailing team's scoring going to the
trailing side and its leader's to the leader (a tie: the better of the two endings goes to each side in proportion
to its mean points, as overtime's winner is drawn). So a tied or close game is decided by a field goal
as often as real ones are, a team down 4-8 chases a touchdown, and a big lead is run out. Real margins of exactly 3
or 7 come from this: two teams scoring independently land on 3 about half as often.

The ending's points go to real players: a touchdown by a catch to a receiver (one more target, catch and pass
attempt, `td_yards` on the play, so the passer's line still is his receivers' sum), a run to a rusher, each drawn
in proportion to his expected TDs among the players still in the game; field goals by distance band, extra points
and misses to the kicker (to a fill-in when the kicker left hurt: points, no line); return TDs and safeties to the
defense. A side with no one to credit (no receivers or rushers) still scores the points.

The draw before the cut is centred without the window's and overtime's mean points, and `expected` adds their
mean events back to the players, so projections stay unbiased: sim_closing_means, which works them out from the
game's expected lead and total.
"""
from collections.abc import Mapping, Sequence
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

import numpy as np

from services.sim_constants import fitted_json, params

CLOSING_PATH = Path(__file__).with_name("sim_closing.json")
CLOSING_FIT = "python -m services.sim_closing_fit --fit"
TD_POINTS = 6
SAFETY_POINTS = 2
BANDS = ("0_39", "40_49", "50")
SECTIONS = ("window", "events", "table", "td_yards", "ot", "line_script")


@lru_cache(maxsize=1)
def stored() -> Mapping:
    """sim_closing.json, read-only, with each margin's endings as (shares, [(trailer events, leader events)]); one
    clear error naming the file and the fit when a section or an interior margin is missing."""
    found = fitted_json(CLOSING_PATH, CLOSING_FIT, SECTIONS)
    events, table = found["events"], {}
    for margin, rows in found["table"].items():
        counts = np.array([r[0] for r in rows], dtype=float)
        table[int(margin)] = (counts / counts.sum(), [(dict(zip(events, r[1])), dict(zip(events, r[2]))) for r in rows])
    missing = sorted(set(range(max(table, default=-1) + 1)) - set(table))
    if not table or missing:
        raise RuntimeError(f"{CLOSING_PATH.name} has no endings for margins {missing or 'any'}: run {CLOSING_FIT}")
    return MappingProxyType({**found, "rows": table, "top": max(table)})


def points_of(events: Mapping[str, float]) -> float:
    """The points of one side's window events (a real ending, or their means)."""
    return (TD_POINTS * (events["pass_td"] + events["rush_td"] + events["ret_td"]) + events["xp"] + 2 * events["two"]
            + 3 * sum(events[f"fg_{b}"] for b in BANDS) + SAFETY_POINTS * events["safety"])


_points = points_of


def line_script() -> Mapping[str, float]:
    """How a team's targets and carries follow its expected margin, log volume per point: the real slope
    (sim_closing_fit) + the tuned `game.line_script_extra` (the simulated game on its own moves volume against the
    line, carries about -0.2% a point: the extra is set so sim_eval's "per line point" rows match real ones)."""
    real = stored()["line_script"]
    extra = params()["game"].get("line_script_extra", {})
    return {k: float(real[k]) + float(extra.get(k, 0.0)) for k in ("targets", "carries")}


def ot() -> Mapping:
    """Overtime as real games played it: tie share, field-goal share of the decided ones, TD play yards."""
    return stored()["ot"]


def ending(margin: int, rng: np.random.Generator) -> tuple[dict, dict]:
    """(trailer's events, leader's events) of a real game that stood at `margin` (>= 0) at the cut."""
    s = stored()
    shares, rows = s["rows"][min(margin, s["top"])]
    return rows[int(rng.choice(len(rows), p=shares))]


# ---------------- crediting the points ----------------

def score_td(side, m: dict, o, kind: str | None, yards: Mapping[str, float],
             rng: np.random.Generator) -> tuple[object, int, int]:
    """(offense, extra target, extra carry) with one TD credited: by a catch (`kind` "rec"), a run ("rush") or
    either (None, overtime), to a player drawn by his expected TDs, never one who left the game hurt. A pass with
    no receiver to credit goes to a rusher, and the reverse; with neither, nobody's line changes."""
    left = side.exited
    rec = m["rec_td"][:-1] * np.array([pid not in left for pid in side.receivers[:-1]], dtype=float)
    rush = m["rush_td"][:-1] * np.array([pid not in left for pid in side.rushers[:-1]], dtype=float)
    if kind == "rec" and rec.sum() <= 0 or kind == "rush" and rush.sum() <= 0:
        kind = None
    weights = np.concatenate([rec if kind != "rush" else 0 * rec, rush if kind != "rec" else 0 * rush])
    if weights.sum() <= 0:
        return o, 0, 0
    pick = int(rng.choice(len(weights), p=weights / weights.sum()))
    if pick < len(rec):
        one = np.eye(len(o.targets), dtype=int)[pick]
        return replace(o, targets=o.targets + one, catches=o.catches + one, attempts=o.attempts + 1,
                       rec_yds=o.rec_yds + int(round(yards["rec"])) * one, rec_td=o.rec_td + one), 1, 0
    one = np.eye(len(o.carries), dtype=int)[pick - len(rec)]
    return replace(o, carries=o.carries + one, rush_yds=o.rush_yds + int(round(yards["rush"])) * one,
                   rush_td=o.rush_td + one), 0, 1


def _kicks(counts: dict, events: Mapping[str, int], kicker_in: bool) -> dict:
    if not kicker_in:                                   # a kicker who left hurt: a fill-in kicked them
        return counts
    made = counts["made"] + np.array([events[f"fg_{b}"] for b in BANDS])
    missed = counts["missed"] + np.array([events[f"miss_{b}"] for b in BANDS])
    return {"made": made, "missed": missed, "pat_made": counts["pat_made"] + events["xp"],
            "pat_att": counts["pat_att"] + events["xp"] + events["xp_miss"]}


def close(sides: Sequence, offense: list, scored: list, points: list, returns: list, safeties: list,
          n_targets: np.ndarray, n_carries: np.ndarray, rng: np.random.Generator, means: list,
          expect: list) -> tuple[list, list, list, list, list, np.ndarray, np.ndarray]:
    """The game after its closing window (see the module docstring); every argument is per side and returned
    updated as (offense, scored, points, returns, safeties, n_targets, n_carries). `expect`: each side's mean
    points (who gets the better ending of a tie)."""
    margin = points[0] - points[1]
    first, second = ending(abs(margin), rng)
    if margin == 0:                                     # the better ending to each side by its mean points
        better, worse = (first, second) if _points(first) >= _points(second) else (second, first)
        events = [better, worse] if rng.random() < expect[0] / sum(expect) else [worse, better]
    else:
        events = [first, second] if margin < 0 else [second, first]
    offense, scored, points, returns, safeties = list(offense), list(scored), list(points), list(returns), list(safeties)
    n_targets, n_carries = n_targets.copy(), n_carries.copy()
    yards = stored()["td_yards"]
    for i, ev in enumerate(events):
        for kind, n in (("rec", ev["pass_td"]), ("rush", ev["rush_td"])):
            for _ in range(n):
                offense[i], extra_target, extra_carry = score_td(sides[i], means[i], offense[i], kind, yards, rng)
                n_targets[i] += extra_target
                n_carries[i] += extra_carry
        scored[i] = (scored[i][0] + _points(ev), _kicks(scored[i][1], ev, sides[i].kicker_share >= 1.0))
        points[i] += _points(ev)
        returns[i] += ev["ret_td"]
        safeties[i] += ev["safety"]
    return offense, scored, points, returns, safeties, n_targets, n_carries
