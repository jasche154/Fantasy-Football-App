"""Replay League defense ratings: how much each NFL defense gives up to a position, as known before a week.

Skeleton: `load_prior` returns nothing and every rating is None (no chip). Worker A fills in the solve
(schedule_strength._solve on data.actual / team_by_week / schedule.opponent) blended with last season's prior:
factor = solved + (prior - 1) x 0.5 x PRIOR_GAMES / (games + PRIOR_GAMES).
"""
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from services.replay_pool import SeasonData

PRIOR_GAMES = 4          # games of this season that weigh as much as last season's half-strength prior


def load_prior(session: Session, season: int, scoring: str) -> Mapping[tuple[str, str], float]:
    """(defense, position) -> last season's factor; empty when last season isn't loaded."""
    return {}


@dataclass(frozen=True)
class Ratings:
    def rating(self, defense: str | None, position: str) -> dict | None:
        """{rank, of, tier, factor, allowed}, or None for K/DST, an unknown team or nothing known."""
        return None


def ratings(data: "SeasonData") -> Ratings:
    """Ratings from `data` (an as_of copy; reads only actual, team_by_week, schedule.opponent, defense_prior)."""
    return Ratings()
