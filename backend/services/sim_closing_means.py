"""Simulated seasons: the mean of a game's closing window and overtime, for centring (sim_centre) and projections
(sim_game.expected). The draw itself is sim_closing.close and sim_draw._overtime.

A game's margin at the cut (ten minutes before the end of regulation) is spread around the two sides' expected
lead d with sd `cut_sd`, which grows with each side's expected points before the cut: sd^2 = a x (pa^b + pb^b),
sim_closing.json's `cut_var`, measured on simulated games by sim_closing_fit.cut_spread (a = 13.4, b = 0.50: about
8.3 for a 5-8 game, 10.4 for 16-16, 11.9 for 30-25; a lopsided game spreads less than an even one at the same
total, which a single power of the total missed by up to 10%). Re-measure it (sim_closing_fit --fit) whenever
the draw before the cut changes (`game.td_drives`, the volumes, the kicking): tests/test_sim_closing checks it
against fresh draws at totals of about 14 and 56. Over a normal spread of whole-point cut margins
(-SPAN..SPAN) the real endings (sim_closing.json's table) give
- each side's mean window events (`window_events(d, s)`): a trailer scores more in the window, a leader less, and
  the closer the cut, the more both kick;
- the chance regulation ends tied (`tie_chance(d, s)`): the share of the endings at each margin that close it
  exactly, so the window's pull toward a tie is in it, as no normal density of the end margin would be;
- overtime's mean points per side (`ot_points`): that chance x (1 - overtime's tie share) x the side's share of
  the two sides' mean points before the cut (who wins it, as the draw has it) x the winning score, a field goal
  (overtime's field-goal share) or a touchdown.

`regulation(a, b)`: the points each side's draw before the cut is centred on so that it plus the window plus
overtime averages a and b (a fixed point: the window and overtime depend on the lead and the total they leave).
`Ending` carries one side's (d, s, share) for `expected`, which adds the window's and overtime's mean events to the
players (`credit_means`, `kicker_points`, `defense_points`).
"""
import math
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType, SimpleNamespace
from typing import TYPE_CHECKING

import numpy as np

from services import kdst_model, sim_closing
from services.sim_closing import BANDS

if TYPE_CHECKING:
    from services.sim_game import _Side

SPAN = 70                        # whole-point cut margins averaged over (-SPAN..SPAN)
MARGINS = np.arange(-SPAN, SPAN + 1)
STEPS = 6                        # fixed-point steps in `regulation` (converged to under 0.01 points by 4)


@dataclass(frozen=True)
class Ending:
    lead: float                  # the side's expected lead at the cut
    sd: float                    # the cut margin's spread
    share: float                 # the side's share of the two sides' mean points before the cut (wins overtime)

    @property
    def window(self) -> float:
        return window_points(self.lead, self.sd)

    @property
    def overtime(self) -> float:
        return ot_points(self.lead, self.sd, self.share)

    @property
    def ot_wins(self) -> float:
        """The chance overtime is decided in this side's favour."""
        return tie_chance(self.lead, self.sd) * (1.0 - float(sim_closing.ot()["tie"])) * self.share


def cut_sd(pre_a: float, pre_b: float) -> float:
    """The spread of a game's margin at the cut, from its two sides' expected points before the cut."""
    a, b = sim_closing.stored()["cut_var"]
    return math.sqrt(a * (max(1.0, pre_a) ** b + max(1.0, pre_b) ** b))


@lru_cache(maxsize=1)
def _tables() -> tuple[np.ndarray, np.ndarray]:
    """(mean events in EVENTS order of a side leading by m at the cut, the chance an ending at m leaves the game
    tied), m = -SPAN..SPAN (row m + SPAN)."""
    s = sim_closing.stored()
    events, rows, ties = s["events"], [], []
    for m in MARGINS:
        shares, endings = s["rows"][min(abs(int(m)), s["top"])]
        trail = np.array([[e[0][k] for k in events] for e in endings], dtype=float)
        lead = np.array([[e[1][k] for k in events] for e in endings], dtype=float)
        rows.append(shares @ ((trail + lead) / 2 if m == 0 else lead if m > 0 else trail))
        closes = np.array([sim_closing.points_of(t) - sim_closing.points_of(ld) == abs(int(m)) for t, ld in endings])
        ties.append(float(shares @ closes))
    return np.array(rows), np.array(ties)


@lru_cache(maxsize=8192)
def _at(d: float, s: float) -> tuple[Mapping[str, float], float]:
    weights = np.exp(-0.5 * ((MARGINS - d) / s) ** 2)
    weights /= weights.sum()
    rows, ties = _tables()
    return MappingProxyType(dict(zip(sim_closing.stored()["events"], weights @ rows))), float(weights @ ties)


def _key(d: float, s: float) -> tuple[float, float]:
    return round(float(d), 1), round(float(s), 2)


def window_events(d: float, s: float) -> Mapping[str, float]:
    """A side's mean window events when it is expected to lead by `d` at the cut, the margin spread `s`."""
    return _at(*_key(d, s))[0]


def window_points(d: float, s: float) -> float:
    return float(sim_closing.points_of(window_events(d, s)))


def window_yards(d: float, s: float) -> float:
    """A side's mean yards on its window TD plays (credited to the scorer on top of the draw's yards)."""
    ev, yards = window_events(d, s), sim_closing.stored()["td_yards"]
    return float(ev["pass_td"] * yards["rec"] + ev["rush_td"] * yards["rush"])


def tie_chance(d: float, s: float) -> float:
    """The chance regulation ends tied (the same for either side: `d`'s sign does not matter)."""
    return _at(*_key(abs(d), s))[1]


def _winning_points() -> float:
    field_goal = float(sim_closing.ot()["field_goal"])
    return 3 * field_goal + 6 * (1 - field_goal)


def ot_points(d: float, s: float, share: float) -> float:
    """A side's mean overtime points (see the module docstring)."""
    return tie_chance(d, s) * (1.0 - float(sim_closing.ot()["tie"])) * share * _winning_points()


def regulation(a: float, b: float, floor: float = 1.0) -> tuple[float, float, Ending, Ending]:
    """(a's and b's points before the cut, their Endings) for a game whose sides should average `a` and `b` with
    the window and overtime in; each kept at `floor` or more."""
    pre_a, pre_b = a, b
    for _ in range(STEPS):
        ends = endings(pre_a, pre_b)
        pre_a = max(floor, a - ends[0].window - ends[0].overtime)
        pre_b = max(floor, b - ends[1].window - ends[1].overtime)
    return pre_a, pre_b, *endings(pre_a, pre_b)


def endings(pre_a: float, pre_b: float) -> tuple[Ending, Ending]:
    """Both sides' Endings from their mean points before the cut."""
    s, share = cut_sd(pre_a, pre_b), pre_a / (pre_a + pre_b) if pre_a + pre_b > 0 else 0.5
    return Ending(pre_a - pre_b, s, share), Ending(pre_b - pre_a, s, 1.0 - share)


# ---------------- crediting the means, for projections ----------------

def credit_means(lines: dict[int, dict[str, float]], side: "_Side", m: dict, end: Ending) -> None:
    """Add the window's mean TDs and overtime's mean winning TD to `side`'s players' mean lines
    (sim_game._expected_players' dicts, by player), as the draw credits them (sim_closing.score_td): window TDs by
    kind, by expected TDs among the players the draw credits, with their catches, targets and yards; an overtime TD
    by catch or run alike, by the same weights; the passer's line follows his receivers'."""
    per, s = window_events(end.lead, end.sd), sim_closing.stored()
    rec, rush = m["rec_td"][:-1], m["rush_td"][:-1]
    ot_td = end.ot_wins * (1.0 - float(s["ot"]["field_goal"]))
    either = rec.sum() + rush.sum()
    ot_rec = ot_td * rec.sum() / either if either > 0 else 0.0
    has_rec, has_rush, total = rec.sum() > 0, rush.sum() > 0, per["pass_td"] + per["rush_td"]
    passes = (per["pass_td"] if has_rush else total) if has_rec else 0.0     # as score_td falls back
    runs = total - passes if has_rush else 0.0
    plays = ((passes, s["td_yards"]["rec"]), (ot_rec, s["ot"]["td_yards"]["rec"]))
    runs_plays = ((runs, s["td_yards"]["rush"]), ((ot_td - ot_rec) if either > 0 else 0.0, s["ot"]["td_yards"]["rush"]))
    for i, pid in enumerate(side.receivers[:-1]):
        line = lines.setdefault(pid, {})
        for n, yards in plays:
            share = n * rec[i] / rec.sum() if n and rec.sum() > 0 else 0.0
            for key, add in (("targets", share), ("rec", share), ("rec_yds", share * yards), ("rec_td", share)):
                line[key] = line.get(key, 0.0) + add
    for i, pid in enumerate(side.rushers[:-1]):
        line = lines.setdefault(pid, {})
        for n, yards in runs_plays:
            share = n * rush[i] / rush.sum() if n and rush.sum() > 0 else 0.0
            for key, add in (("carries", share), ("rush_yds", share * yards), ("rush_td", share)):
                line[key] = line.get(key, 0.0) + add
    thrown = sum(n for n, _ in plays) if has_rec else 0.0
    if side.qb is not None and thrown:
        line = lines.setdefault(side.qb, {})
        line["pass_yds"] = line.get("pass_yds", 0.0) + sum(n * yards for n, yards in plays)
        line["pass_td"] = line.get("pass_td", 0.0) + thrown


def kicker_points(end: Ending, bands: list[float]) -> float:
    """A kicker's mean points from the window (its field goals by band, misses and extra points) and from an
    overtime field goal (drawn in a band by `bands`, the kick bands' shares)."""
    per = window_events(end.lead, end.sd)
    ot_fg = end.ot_wins * float(sim_closing.ot()["field_goal"])
    share = np.array(bands) / sum(bands)
    row = SimpleNamespace(**{f"made_{b}": per[f"fg_{b}"] + ot_fg * share[j] for j, b in enumerate(BANDS)},
                          **{f"missed_{b}": per[f"miss_{b}"] for b in BANDS},
                          pat_made=per["xp"], pat_att=per["xp"] + per["xp_miss"])
    return float(kdst_model._actual_k(row))


def defense_points(end: Ending) -> float:
    """A defense's mean points from the window (return TDs, safeties), points allowed aside."""
    per = window_events(end.lead, end.sd)
    blank = SimpleNamespace(def_sacks=0, def_ints=0, def_fumble_rec=0, def_tds=0, def_safeties=0, def_blocks=0,
                            points_against=1000)
    scored = SimpleNamespace(**{**vars(blank), "def_tds": per["ret_td"], "def_safeties": per["safety"]})
    return float(kdst_model._actual_dst(scored) - kdst_model._actual_dst(blank))
