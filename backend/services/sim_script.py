"""Simulated seasons: play calling that follows the score, and depth players' lumpy work (read by sim_draw and
sim_game._side_means; fitted by sim_script_fit into sim_script.json).

A two-team game is drawn in three parts (sim_draw): the first half, the second half up to the closing window's cut
and the window (sim_closing). A side's mean targets and carries in each half are its game volume x the half's share
(`shares`, real 2018-2025: about 50% and 30%; the window has the rest) x (1 + the half's slope per point of the
side's expected margin, the pregame line) and, in the second half, + its slope per point of the margin at half
time: a team down two scores after the half throws about 20% more and runs about 13% less. The draw applies the
real margin at half time; its mean, `half_lead`, is what the projections use (`parts`), so they stay the draw's
mean. The window's plays are a real game's ending (sim_closing.json's table, the endings' counts scaled by the
side's volume against the league's): the leader runs and kneels, the trailer throws.

Kneels are the quarterback's who finishes the game (`kneeler`). The starter's belief carries count his kneels (the
box does), so in a game in parts his other carries are his belief carries less his own expected kneels
(`carry_weights`), at the yards and TDs per carry that keep his belief yards and TDs (`rushing`): his projection is
the belief's. The window's plays never go to a player who left the game hurt (`window_shares`).

Only plays before the cut score: their TD rates are divided by the halves' share of the game's volume (`before_cut`),
so the game's mean touchdowns are what a whole game's volume at the per-play rates gives (the window's scoring is
its real ending's); interceptions and sacks likewise come from the passes before the cut. Yards come from every
play.

`plan_weights`: a player is in the game plan with chance q = 1 - c exp(-touches / scale) by his position and the
touches the belief expects of him (fitted, sim_script_fit); a player with q of CORE_Q or more always is (the curve
is within 3% of 1 there), and so is one with CORE_SHARE of his team's targets or carries (a lead back: the players
always in must hold real work to take up the others', or the carries' would fall to receivers with a carry a game).
In the plan his share is his weight's / q, out of it nothing; the always-in players (not the "other" bucket, not
the quarterback's carries, which are drawn on their own) take up the difference in proportion, so every share
averages the weight's share and the projections stay the draw's mean.
"""
import math
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

import numpy as np

from dataclasses import dataclass

from services.sim_constants import fitted_json

SCRIPT_PATH = Path(__file__).with_name("sim_script.json")
SCRIPT_FIT = "python -m services.sim_script_fit --fit"
SECTIONS = ("shares", "first_half", "second_half", "spread", "league", "participation")
CORE_Q = 0.97                    # a player this likely to be in the game plan always is
CORE_SHARE = 0.25                # ... and so is one with this share of his team's targets or carries


@lru_cache(maxsize=1)
def stored() -> Mapping:
    return fitted_json(SCRIPT_PATH, SCRIPT_FIT, SECTIONS)


def before_cut(stat: str) -> float:
    """The halves' share of a game's `stat` ("targets" or "carries")."""
    shares = stored()["shares"][stat]
    return float(shares[0] + shares[1])


def scoring_share(side) -> tuple[float, float]:
    """The share of a game's targets and of its carries the side's TDs, interceptions and sacks come from: the
    halves' (`before_cut`) in a game in parts, all of it (1, 1) for a side without one."""
    if side.end is None:
        return 1.0, 1.0
    return before_cut("targets"), before_cut("carries")


def half_share() -> float:
    """The first half's share of the plays before the cut (and so of the points scored there)."""
    s = stored()["shares"]
    return float((s["targets"][0] + s["carries"][0]) / (before_cut("targets") + before_cut("carries")))


def second_half(stat: str, margin: float, lead: float) -> float:
    """The second half's volume multiplier for a side expected to win by `margin` that leads by `lead` at half time
    (never below 0; linear, so its mean is the multiplier at the mean lead)."""
    slope = stored()["second_half"][stat]
    return max(0.0, 1.0 + slope["spread"] * margin + slope["margin"] * lead)


def parts(side) -> np.ndarray:
    """[[first-half targets, carries], [second-half targets, carries]]: the side's mean volume in each half
    (`side.margin` its expected margin, `side.half_lead` its mean lead at half time). Read-only (cached)."""
    return _parts(float(side.targets), float(side.carries), float(side.margin), float(side.half_lead))


@lru_cache(maxsize=8192)
def _parts(targets: float, carries: float, margin: float, half_lead: float) -> np.ndarray:
    s, first = stored()["shares"], stored()["first_half"]
    out = np.empty((2, 2))
    for j, (stat, volume) in enumerate((("targets", targets), ("carries", carries))):
        out[0, j] = volume * s[stat][0] * max(0.0, 1.0 + first[stat] * margin)
        out[1, j] = volume * s[stat][1] * second_half(stat, margin, half_lead)
    out.setflags(write=False)
    return out


def window_scale(side) -> tuple[float, float]:
    """(targets, carries) multipliers of a real ending's play counts for this side: its volume over the league's."""
    league = stored()["league"]
    return side.targets / league["targets"], side.carries / league["carries"]


def kneel_yards() -> float:
    return float(stored()["league"]["kneel_yards"])


def kneeler(side) -> int | None:
    """The quarterback who finishes the game and takes its kneels: the starter, else (he left hurt) his backup."""
    return side.backup if side.qb in side.exited and side.backup is not None else side.qb


def _qb_carries(side) -> tuple[int, float, float, float]:
    """(his index among the rushers, his belief carries in the game, his own expected kneels, his other carries)
    for the starting QB of a side in parts."""
    at = side.rushers.index(side.qb)
    share = float(side.carry_w[at] / side.carry_w.sum())
    plays = parts(side)[:, 1].sum()
    _, window, kneels = window_means(side)
    own = kneels if kneeler(side) == side.qb else 0.0
    team = plays + window + kneels
    return at, share * team, own, max(0.0, share * team - own)


def carry_weights(side) -> np.ndarray:
    """The side's carry weights over its carries besides kneels: in a game in parts the starting QB's is cut to his
    belief carries less his own expected kneels (the others' keep their proportions); else `carry_w`."""
    if side.end is None or side.qb is None:
        return side.carry_w
    at, _, _, other = _qb_carries(side)
    plays = parts(side)[:, 1].sum() + window_means(side)[1]
    share = min(other / plays, 0.99) if plays > 0 else 0.0
    rest = side.carry_w.sum() - side.carry_w[at]
    out = side.carry_w.copy()
    out[at] = share * rest / (1.0 - share) if rest > 0 else out[at]
    return out


def rushing(side) -> tuple[np.ndarray, np.ndarray]:
    """(yards per carry, TDs per carry) of the side's rushers on carries that are not kneels: in a game in parts the
    starting QB's are the ones that keep his belief yards and TDs once his kneels (sim_script.json's `kneel_yards`
    each, never a TD) are taken out; else the side's rates."""
    if side.end is None or side.qb is None:
        return side.ypr, side.rush_td
    at, belief, own, other = _qb_carries(side)
    if other <= 0:
        return side.ypr, side.rush_td
    ypr, td = side.ypr.copy(), side.rush_td.copy()
    ypr[at] = (belief * side.ypr[at] - own * kneel_yards()) / other
    td[at] = belief * side.rush_td[at] / other
    return ypr, td


def window_means(side) -> tuple[float, float, float]:
    """(targets, carries, kneels) the side's window adds besides its scoring plays, on average (sim_closing_means
    over the cut margin's spread): the ending's targets less its TD catches and its carries less its TD runs and
    kneels, scaled by `window_scale`; kneels as they come."""
    t_scale, c_scale = window_scale(side)
    targets, carries, kneels = _window(float(side.end.lead), float(side.end.sd))
    return t_scale * targets, c_scale * carries, kneels


@lru_cache(maxsize=8192)
def _window(lead: float, sd: float) -> tuple[float, float, float]:
    from services import sim_closing_means
    ev = sim_closing_means.window_events(lead, sd)
    return (max(0.0, ev["targets"] - ev["pass_td"]), max(0.0, ev["carries"] - ev["rush_td"] - ev["kneels"]),
            float(ev["kneels"]))


def window_plays(side, events: Mapping[str, int], rng: np.random.Generator) -> tuple[int, int, int]:
    """(targets, carries, kneels) of one drawn ending's non-scoring plays: scaled counts rounded at random (the
    mean exact), kneels as they come."""
    t_scale, c_scale = window_scale(side)
    targets = t_scale * max(0, events["targets"] - events["pass_td"])
    carries = c_scale * max(0, events["carries"] - events["rush_td"] - events["kneels"])
    return _round(targets, rng), _round(carries, rng), int(events["kneels"])


def _round(x: float, rng: np.random.Generator) -> int:
    whole = math.floor(x)
    return int(whole + (rng.random() < x - whole))


# ---------------- the game plan ----------------

def in_plan(touches: float, position: str) -> float:
    """The chance a player at `position` expected `touches` touches is in the game plan (1 from CORE_Q up, and at
    a position with no curve)."""
    p = stored()["participation"].get(position)
    if p is None:
        return 1.0
    q = 1.0 - p["c"] * math.exp(-touches / p["scale"])
    return 1.0 if q >= CORE_Q else max(1e-3, q)


def plan_weights(weights: np.ndarray, q: np.ndarray, drawn: np.ndarray, fixed: np.ndarray) -> np.ndarray:
    """`weights` for one game: a player with q < 1 is in (`drawn` True) at weight / q or out at 0, and the players
    always in take up the difference in proportion; `fixed` players (the "other" bucket, the QB's carries) keep
    theirs. Linear in the draws, so each weight's mean is itself. Should the in-plan players outweigh every
    always-in one (a thin roster), the always-in keep nothing more and the rest is renormalized."""
    w = np.asarray(weights, dtype=float)
    depth = (q < 1.0) & ~fixed
    core = (q >= 1.0) & ~fixed
    out = w.copy()
    out[depth] = np.where(drawn[depth], w[depth] / q[depth], 0.0)
    room = w[depth].sum() + w[core].sum()
    taken = out[depth].sum()
    if core.any() and w[core].sum() > 0 and taken <= room:
        out[core] = w[core] * (room - taken) / w[core].sum()
    elif out[depth | core].sum() > 0:                       # shortcut: a thin roster, renormalized (biased, rare)
        out[depth | core] *= room / out[depth | core].sum()
    return out


@dataclass(frozen=True)
class PlanInputs:
    """Who may sit out of a side's game plan: each such player's chance to be in it (`q`) and where he sits among
    the receivers' and the rushers' weights (an index into `q`; -1: always in, the "other" bucket and QBs)."""
    q: np.ndarray
    receivers: np.ndarray
    rushers: np.ndarray


def plan_inputs(receivers: tuple, target_w: np.ndarray, rushers: tuple, carry_w: np.ndarray, targets: float,
                carries: float, positions: Mapping[int, str]) -> PlanInputs:
    """The side's PlanInputs from its weights before anyone leaves the game hurt (so an exit never takes a lead
    player out of the plan): each player's expected touches at the side's volume, and a lead player (CORE_SHARE of
    the targets or carries) always in."""
    t_share, c_share = target_w / target_w.sum(), carry_w / carry_w.sum()
    touches: dict[int, float] = {}
    lead: set[int] = set()
    for ids, share, volume in ((receivers, t_share, targets), (rushers, c_share, carries)):
        for pid, s in zip(ids[:-1], share[:-1]):
            if positions.get(pid) != "QB":
                touches[pid] = touches.get(pid, 0.0) + float(s) * volume
                if s >= CORE_SHARE:
                    lead.add(pid)
    players = list(touches)
    q = np.array([1.0 if pid in lead else in_plan(touches[pid], positions.get(pid, "")) for pid in players])
    index = {pid: i for i, pid in enumerate(players)}
    return PlanInputs(q, np.array([index.get(pid, -1) for pid in receivers]),
                      np.array([index.get(pid, -1) for pid in rushers]))


def window_shares(shares: np.ndarray, ids: tuple, exited: frozenset) -> np.ndarray:
    """`shares` without the players who left the game hurt (the window is the end of the game), renormalized."""
    if not exited:
        return shares
    out = np.where([pid in exited for pid in ids], 0.0, shares)
    total = out.sum()
    return out / total if total > 0 else out
