"""Replay League: which preseason ADP list (replay_adp.format) a league drafts from.

- `wanted`: a superflex league (replay_slots.two_qb: a SUPERFLEX slot, or 2+ QB slots) reads the 2-QB list (and,
  in SeasonData.scoring_adp, its scoring's list: replay_superflex places its QBs among those non-QBs); any other
  league from its scoring's list: ppr / half / std, and custom rules from the preset nearest their points
  per reception (a tie goes to the bigger one: 0.75 -> ppr, 0.25 -> half).
- `choose`: the wanted list if that season has it, else the nearest one it has (2qb -> the scoring's list,
  then the nearest; ppr <-> half <-> std). SeasonData.adp_format records the list actually used, so a
  superflex league without a 2-QB list still gets replay_superflex's fitted QB placement.

ADP is preseason data: the same list serves every week, no hindsight.
"""
import logging
from collections.abc import Collection, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import ReplayAdp
from services import replay_slots
from services.replay_scoring import ScoringRules
from services.scoring import POINTS_PER_RECEPTION

log = logging.getLogger(__name__)

PRESETS = ("ppr", "half", "std")
TWO_QB = "2qb"
FORMATS = (*PRESETS, TWO_QB)
NEAREST = {"ppr": ("ppr", "half", "std"), "half": ("half", "ppr", "std"), "std": ("std", "half", "ppr")}


def scoring_format(scoring: str, rules: ScoringRules | None = None) -> str:
    """The preset list for this scoring: the preset itself, or the one nearest the custom reception points."""
    if rules is None:
        return scoring
    return min(PRESETS, key=lambda p: (abs(POINTS_PER_RECEPTION[p] - rules.reception), -POINTS_PER_RECEPTION[p]))


def wanted(scoring: str, rules: ScoringRules | None = None, starters: Mapping[str, int] | None = None) -> str:
    return TWO_QB if replay_slots.two_qb(starters) else scoring_format(scoring, rules)


def choose(want: str, scoring: str, available: Collection[str]) -> str | None:
    """The list to read: `want` if stored, else the nearest stored one; None when the season has no ADP."""
    order = (TWO_QB, *NEAREST[scoring]) if want == TWO_QB else NEAREST[want]
    return next((f for f in order if f in available), None)


def stored(session: Session, season: int, scoring: str, rules: ScoringRules | None = None,
           want: str | None = None) -> str:
    """The stored list `choose` picks for this league (`want` defaults to the scoring's); "ppr" when none is."""
    available = set(session.scalars(select(ReplayAdp.format).where(ReplayAdp.season == season).distinct()))
    preset = scoring_format(scoring, rules)
    want = want or preset
    chosen = choose(want, preset, available) or "ppr"
    if chosen != want:
        log.info("Replay %s: no %s ADP stored, using %s", season, want, chosen)
    return chosen
