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

The draw before the cut is centred without the window (sim_centre): its touchdowns and field-goal tries are scaled
down by the window's mean points and its yards by the window TDs' mean yards, and `expected` adds the window's mean
events back to the players (`credit_means`), so projections stay unbiased. The window's mean events depend on the
side's expected lead at the cut d (a trailer scores more in it, a leader less): `window_events(d)` averages each
margin's real mean events over a normal spread of margins around d (CUT_SD, the simulated spread of margins at the
cut around their mean).
"""
from collections.abc import Mapping, Sequence
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import numpy as np

from services import kdst_model
from services.sim_constants import fitted_json

CLOSING_PATH = Path(__file__).with_name("sim_closing.json")
CLOSING_FIT = "python -m services.sim_closing_fit --fit"
TD_POINTS = 6
SAFETY_POINTS = 2
BANDS = ("0_39", "40_49", "50")
CUT_SD = 11.1                    # sd of a simulated margin at the cut around its mean (2,272 games, sim_eval 2022-24)
SPAN = 70                        # margins at the cut averaged over (-SPAN..SPAN)


@lru_cache(maxsize=1)
def stored() -> Mapping:
    found = fitted_json(CLOSING_PATH, CLOSING_FIT, ("window", "events", "table", "per_side", "td_yards", "ot"))
    events = found["events"]
    table = {}
    for margin, rows in found["table"].items():
        counts = np.array([r[0] for r in rows], dtype=float)
        table[int(margin)] = (counts / counts.sum(), [(dict(zip(events, r[1])), dict(zip(events, r[2]))) for r in rows])
    return MappingProxyType({**found, "rows": table, "top": max(table)})


def _points(events: Mapping[str, int]) -> int:
    return (TD_POINTS * (events["pass_td"] + events["rush_td"] + events["ret_td"]) + events["xp"] + 2 * events["two"]
            + 3 * sum(events[f"fg_{b}"] for b in BANDS) + SAFETY_POINTS * events["safety"])


@lru_cache(maxsize=1)
def _by_margin() -> np.ndarray:
    """Mean events (EVENTS order) of a side leading by m at the cut, m = -SPAN..SPAN (row m + SPAN)."""
    s = stored()
    events = s["events"]
    rows = []
    for m in range(-SPAN, SPAN + 1):
        shares, endings = s["rows"][min(abs(m), s["top"])]
        trail = np.array([[e[0][k] for k in events] for e in endings], dtype=float)
        lead = np.array([[e[1][k] for k in events] for e in endings], dtype=float)
        mine = (trail + lead) / 2 if m == 0 else lead if m > 0 else trail
        rows.append(shares @ mine)
    return np.array(rows)


@lru_cache(maxsize=4096)
def _events_at(d: float) -> Mapping[str, float]:
    margins = np.arange(-SPAN, SPAN + 1)
    weights = np.exp(-0.5 * ((margins - d) / CUT_SD) ** 2)
    return MappingProxyType(dict(zip(stored()["events"], weights @ _by_margin() / weights.sum())))


def window_events(d: float = 0.0) -> Mapping[str, float]:
    """A side's mean closing-window events when it is expected to lead by `d` points at the cut."""
    return _events_at(round(float(d), 1))


def window_points(d: float = 0.0) -> float:
    """A side's mean points in the closing window when it is expected to lead by `d` at the cut."""
    return float(_points(window_events(d)))


def window_yards(d: float = 0.0) -> float:
    """A side's mean yards on its closing-window TD plays (credited to the scorer on top of the draw's yards)."""
    ev, yards = window_events(d), stored()["td_yards"]
    return float(ev["pass_td"] * yards["rec"] + ev["rush_td"] * yards["rush"])


def cut_lead(wanted: float, other: float, steps: int = 3) -> float:
    """The expected lead at the cut of a side wanted at `wanted` points against `other` (both window included):
    d = wanted - other - (its window points at d - the other's at -d), solved by a few fixed-point steps."""
    d = wanted - other
    for _ in range(steps):
        d = wanted - other - (window_points(d) - window_points(-d))
    return d


def ot() -> Mapping:
    """Overtime as real games played it: tie share, field-goal share of the decided ones, TD play yards."""
    return stored()["ot"]


def ending(margin: int, rng: np.random.Generator) -> tuple[dict, dict]:
    """(trailer's events, leader's events) of a real game that stood at `margin` (>= 0) at the cut."""
    s = stored()
    shares, rows = s["rows"][min(margin, s["top"])]
    return rows[int(rng.choice(len(rows), p=shares))]


# ---------------- crediting the points ----------------

def score_td(side, m: dict, o, kind: str | None, yards: Mapping[str, float], rng: np.random.Generator):
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
          n_targets: np.ndarray, n_carries: np.ndarray, rng: np.random.Generator, means: list, expect: list) -> tuple:
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


# ---------------- the window's means, for projections ----------------

def credit_means(lines: dict[int, dict[str, float]], side, m: dict, d: float) -> None:
    """Add the closing window's mean events (`window_events(d)`, d: the side's expected lead at the cut) to `side`'s
    players' mean lines (sim_game._expected_players' dicts, by player): TDs shared by expected TDs among the players
    the draw credits, with their catches, targets and yards; the passer's line follows his receivers'."""
    per, yards = window_events(d), stored()["td_yards"]
    rec, rush = m["rec_td"][:-1], m["rush_td"][:-1]
    has_rec, has_rush, total = rec.sum() > 0, rush.sum() > 0, per["pass_td"] + per["rush_td"]
    passes = (per["pass_td"] if has_rush else total) if has_rec else 0.0    # as score_td falls back
    runs = total - passes if has_rush else 0.0
    for i, pid in enumerate(side.receivers[:-1]):
        share = passes * rec[i] / rec.sum() if passes else 0.0
        line = lines.setdefault(pid, {})
        for key, add in (("targets", share), ("rec", share), ("rec_yds", share * yards["rec"]), ("rec_td", share)):
            line[key] = line.get(key, 0.0) + add
    for i, pid in enumerate(side.rushers[:-1]):
        share = runs * rush[i] / rush.sum() if runs else 0.0
        line = lines.setdefault(pid, {})
        for key, add in (("carries", share), ("rush_yds", share * yards["rush"]), ("rush_td", share)):
            line[key] = line.get(key, 0.0) + add
    if side.qb is not None and passes:
        line = lines.setdefault(side.qb, {})
        line["pass_yds"] = line.get("pass_yds", 0.0) + passes * yards["rec"]
        line["pass_td"] = line.get("pass_td", 0.0) + passes


def kicker_points(d: float) -> float:
    """A kicker's mean points from the closing window (its field goals by band, misses and extra points)."""
    per = window_events(d)
    row = SimpleNamespace(**{f"made_{b}": per[f"fg_{b}"] for b in BANDS}, **{f"missed_{b}": per[f"miss_{b}"] for b in BANDS},
                          pat_made=per["xp"], pat_att=per["xp"] + per["xp_miss"])
    return float(kdst_model._actual_k(row))


def defense_points(d: float) -> float:
    """A defense's mean points from the closing window (return TDs, safeties), points allowed aside."""
    per = window_events(d)
    blank = SimpleNamespace(def_sacks=0, def_ints=0, def_fumble_rec=0, def_tds=0, def_safeties=0, def_blocks=0,
                            points_against=1000)
    scored = SimpleNamespace(**{**vars(blank), "def_tds": per["ret_td"], "def_safeties": per["safety"]})
    return float(kdst_model._actual_dst(scored) - kdst_model._actual_dst(blank))
