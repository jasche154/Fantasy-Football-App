"""Simulated seasons: the closing minutes of a game, played from the score (sim_draw), and overtime's measured rates.

sim_draw draws a game up to the last `window` seconds of regulation (600: ten minutes, chosen with sim_eval; 300
left margins of 3 at 11.0% against a real 14.6%, and 900 only added noise). From there the ending is a real one:
the margin at the cut picks the set of real games that stood at that margin (sim_closing.json's `table`, fitted
from 2018-2025 play-by-play by sim_closing_fit), and one of them is drawn, its trailing team's scoring going to the
trailing side and its leader's to the leader (a tie: the better of the two endings goes to each side in proportion
to its mean points, as overtime's winner is drawn). So a tied or close game is decided by a field goal
as often as real ones are, a team down 4-8 chases a touchdown, and a big lead is run out. Real margins of exactly 3
or 7 come from this: two teams scoring independently land on 3 about half as often.

Its other plays (the leader's runs and kneel-downs, the trailer's throws: the real ending's targets, carries and
kneels, ridden on the table's rows) are shared out by the game plan (sim_parts.window).

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

from services import sim_script
from services.sim_constants import fitted_json

CLOSING_PATH = Path(__file__).with_name("sim_closing.json")
CLOSING_FIT = "python -m services.sim_closing_fit --fit"
TD_POINTS = 6
SAFETY_POINTS = 2
EDGE_WIDTH = 4.0                 # points: the spread kernel's sd, set by hand. Whole seasons: the cut lead follows
                                 # the line 1.08 a point (real 0.99, 1.22 without the kernel). On the stored table
                                 # it gives about two thirds of the real within-margin slope; a narrower one
                                 # leaves big lines too few endings (under 5 effective games in 12% of draws at
                                 # a 17-point line already)
BANDS = ("0_39", "40_49", "50")
SECTIONS = ("window", "events", "table", "td_yards", "ot", "line_script", "cut_var")


@lru_cache(maxsize=1)
def stored() -> Mapping:
    """sim_closing.json, read-only, with each margin's endings as (shares, [(trailer events, leader events)]); one
    clear error naming the file and the fit when a section or an interior margin is missing."""
    found = fitted_json(CLOSING_PATH, CLOSING_FIT, SECTIONS)
    events, table = found["events"], {}
    for margin, rows in found["table"].items():
        counts = np.array([r[0] for r in rows], dtype=float)
        spreads = np.array([float(r[3]) if len(r) > 3 else 0.0 for r in rows])
        table[int(margin)] = (counts / counts.sum(), [(dict(zip(events, r[1])), dict(zip(events, r[2]))) for r in rows],
                              spreads)
    missing = sorted(set(range(max(table, default=-1) + 1)) - set(table))
    if not table or missing:
        raise RuntimeError(f"{CLOSING_PATH.name} has no endings for margins {missing or 'any'}: run {CLOSING_FIT}")
    return MappingProxyType({**found, "rows": table, "top": max(table)})


def points_of(events: Mapping[str, float]) -> float:
    """The points of one side's window events (a real ending, or their means)."""
    return (TD_POINTS * (events["pass_td"] + events["rush_td"] + events["ret_td"]) + events["xp"] + 2 * events["two"]
            + 3 * sum(events[f"fg_{b}"] for b in BANDS) + SAFETY_POINTS * events["safety"])


_points = points_of


def ot() -> Mapping:
    """Overtime as real games played it: tie share, field-goal share of the decided ones, TD play yards."""
    return stored()["ot"]


def ending(margin: int, rng: np.random.Generator, edge: float | None = None) -> tuple[dict, dict]:
    """(trailer's events, leader's events) of a real game that stood at `margin` (>= 0) at the cut, drawn with
    `near(edge)` weights: the trailer's expected margin (None: every ending alike)."""
    s = stored()
    shares, rows, spreads = s["rows"][min(margin, s["top"])]
    return rows[int(rng.choice(len(rows), p=near(shares, spreads, edge)))]


def near(shares: np.ndarray, spreads: np.ndarray, edge: float | None) -> np.ndarray:
    """`shares` x a normal kernel (sd EDGE_WIDTH) on how far each ending's trailer spread is from `edge`, the
    trailing side's expected margin in this game, renormalized: a real favourite closes a game better than an
    underdog at the same margin (0.21 points a point of the spread over the window, 2018-2025)."""
    if edge is None:
        return shares
    weights = shares * np.exp(-0.5 * ((spreads - edge) / EDGE_WIDTH) ** 2)
    total = weights.sum()
    return weights / total if total > 0 else shares


# ---------------- crediting the points ----------------

def score_td(side, m: dict, o, kind: str | None, yards: Mapping[str, float],
             rng: np.random.Generator) -> tuple[object, int, int]:
    """(offense, extra target, extra carry) with one TD credited: by a catch (`kind` "rec"), a run ("rush") or
    either (None, overtime), to a player drawn by his expected TDs x the share of his part he keeps in the window
    (sim_script.window_tds: none for one who left before the cut). A pass with no receiver to credit goes to a
    rusher, and the reverse; with neither, nobody's line changes."""
    rec, rush = sim_script.window_tds(side, m)
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


def pick(points: Sequence[int], expect: Sequence[float], rng: np.random.Generator,
         edges: Sequence[float] | None = None) -> list[dict]:
    """Each side's events in the window: a real ending for the margin at the cut (`points`), near the trailing side's
    expected margin (`edges`, each side's; None: any), the trailer's to the trailing side; a tie's better ending to
    each side in proportion to its mean points (`expect`), drawn near the first side's."""
    margin = points[0] - points[1]
    first, second = ending(abs(margin), rng, None if edges is None else edges[1 if margin > 0 else 0])
    if margin == 0:                                     # the better ending to each side by its mean points
        better, worse = (first, second) if _points(first) >= _points(second) else (second, first)
        return [better, worse] if rng.random() < expect[0] / sum(expect) else [worse, better]
    return [first, second] if margin < 0 else [second, first]


def close(sides: Sequence, offense: list, scored: list, points: list, returns: list, safeties: list,
          n_targets: np.ndarray, n_carries: np.ndarray, rng: np.random.Generator, means: list,
          expect: list, events: list | None = None) -> tuple[list, list, list, list, list, np.ndarray, np.ndarray]:
    """The game after its closing window (see the module docstring); every argument is per side and returned
    updated as (offense, scored, points, returns, safeties, n_targets, n_carries). `expect`: each side's mean
    points (who gets the better ending of a tie); `events`: the endings already picked (`pick`), else picked here.
    Only the endings' scoring plays are credited here (sim_parts.window adds the rest)."""
    events = events if events is not None else pick(points, expect, rng)
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
