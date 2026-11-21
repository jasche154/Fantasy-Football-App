"""Replay League trade chart: every player on one 0-1000 scale, rank-based (two good players can't add up
to a star).

A position rank becomes the ADP pick where that season's Nth player at the position went (`slot_curve`),
and a pick becomes points on a convex curve (`pick_value`). Pure: no database, no Knowledge.
See .plans/pr1-plan.md section C for the formula.
"""
import math
from collections.abc import Mapping, Sequence

from services.replay_pool import SeasonData

TAU = 40.0                     # pick_value decay: 1 -> 1000, 12 -> 760, 24 -> 563, 50 -> 294, 100 -> 84
CHART_MAX = 1000.0
PICKS_PAST_LAST = 3            # slot curve past the last drafted player: this many picks per rank
STAR_TOP, STAR_SPAN = 60, 48   # star = clamp((STAR_TOP - pre_rank) / STAR_SPAN, 0, 1)
FORM_GAMES = 3                 # model_ppg: his last real games
FORM_WEIGHT = 0.50
FORM_STAR_DOWN = 0.6           # a star's dip counts less
FORM_STAR_UP = 0.3             # a breakout below his prior counts more for a star
EXPERT_WEIGHT = 0.65           # blend weight on the expert rank for a snapshot <= EXPERT_FRESH_DAYS old
EXPERT_FRESH_DAYS = 10
UNLISTED_GAP = 10              # missing from an existing snapshot: last listed + this
BYE_BUMP = 1.03                # once his bye is behind him
AVAILABILITY_SHARE = 0.9       # chart x share_played x this, when a news report exists


def slot_curve(data: SeasonData) -> Mapping[str, tuple[float, ...]]:
    """{position: (ADP pick of the 1st, 2nd, ... player drafted there)} for that season."""
    raise NotImplementedError


def pick_value(pick: float) -> float:
    """Chart points for a draft pick: CHART_MAX at pick 1, decaying by TAU."""
    return CHART_MAX * math.exp(-(pick - 1) / TAU)


def value_at(curve: Mapping[str, Sequence[float]], position: str, rank: float) -> float:
    """Chart points for a (fractional, 1-based) position rank, through the slot curve."""
    raise NotImplementedError


def model_ppg(ros_ppg: float, prior: float, recent_points: Sequence[float], star: float) -> float:
    """ros_ppg moved toward his recent real games (newest first), less so for a star's dip."""
    raise NotImplementedError


def star(pre_rank: int | None) -> float:
    """0-1: how much of a preseason star he was (1 inside the top 12, 0 from 60 on or unranked)."""
    if pre_rank is None:
        return 0.0
    return min(1.0, max(0.0, (STAR_TOP - pre_rank) / STAR_SPAN))
