"""Fantasy scoring rules and point calculations.

`fantasy_points` reproduces nflverse's `fantasy_points` (standard) and `fantasy_points_ppr`
columns exactly when given the matching preset, so any custom scoring can be computed
from raw box-score stats.
"""
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal

ScoringPreset = Literal["ppr", "half", "std"]
POINTS_PER_RECEPTION: dict[str, float] = {"ppr": 1.0, "half": 0.5, "std": 0.0}
SCORING_LABELS: dict[str, str] = {"ppr": "PPR", "half": "half-PPR", "std": "standard"}

# nflverse's expected fantasy points (ff_opportunity) are computed with PPR rules.
OPPORTUNITY_RECEPTION_POINTS = 1.0
OPPORTUNITY_PASS_TD_POINTS = 4.0


@dataclass(frozen=True)
class ScoringSettings:
    pass_yd: float = 0.04
    pass_td: float = 4.0
    interception: float = -2.0
    rush_yd: float = 0.1
    rush_td: float = 6.0
    reception: float = 1.0
    rec_yd: float = 0.1
    rec_td: float = 6.0
    te_premium: float = 0.0          # extra points per reception for tight ends
    fumble_lost: float = -2.0
    two_point: float = 2.0
    special_teams_td: float = 6.0


def scoring_for(preset: str, te_premium: float = 0.0) -> ScoringSettings:
    if preset not in POINTS_PER_RECEPTION:
        raise ValueError(f"Unknown scoring preset '{preset}'. Valid: {sorted(POINTS_PER_RECEPTION)}")
    return replace(ScoringSettings(), reception=POINTS_PER_RECEPTION[preset], te_premium=te_premium)


def _num(stats: Mapping[str, Any], key: str) -> float:
    value = stats.get(key)
    return float(value) if value is not None else 0.0


def reception_points(position: str | None, scoring: ScoringSettings) -> float:
    return scoring.reception + (scoring.te_premium if position == "TE" else 0.0)


def fantasy_points(stats: Mapping[str, Any], position: str | None, scoring: ScoringSettings) -> float:
    """Points for one player-week from player_stats_weekly columns."""
    fumbles_lost = (_num(stats, "sack_fumbles_lost") + _num(stats, "rushing_fumbles_lost")
                    + _num(stats, "receiving_fumbles_lost"))
    two_points = (_num(stats, "passing_2pt_conversions") + _num(stats, "rushing_2pt_conversions")
                  + _num(stats, "receiving_2pt_conversions"))
    points = (
        _num(stats, "passing_yards") * scoring.pass_yd
        + _num(stats, "passing_tds") * scoring.pass_td
        + _num(stats, "passing_interceptions") * scoring.interception
        + _num(stats, "rushing_yards") * scoring.rush_yd
        + _num(stats, "rushing_tds") * scoring.rush_td
        + _num(stats, "receptions") * reception_points(position, scoring)
        + _num(stats, "receiving_yards") * scoring.rec_yd
        + _num(stats, "receiving_tds") * scoring.rec_td
        + fumbles_lost * scoring.fumble_lost
        + two_points * scoring.two_point
        + _num(stats, "special_teams_tds") * scoring.special_teams_td
    )
    return round(points, 2)


def expected_points(opportunity: Mapping[str, Any], position: str | None,
                    scoring: ScoringSettings) -> float | None:
    """Convert nflverse's PPR-based expected fantasy points to the given scoring."""
    total = opportunity.get("total_fantasy_points_exp")
    if total is None:
        return None
    reception_delta = reception_points(position, scoring) - OPPORTUNITY_RECEPTION_POINTS
    pass_td_delta = scoring.pass_td - OPPORTUNITY_PASS_TD_POINTS
    adjusted = (float(total) + reception_delta * _num(opportunity, "receptions_exp")
                + pass_td_delta * _num(opportunity, "pass_touchdown_exp"))
    return round(adjusted, 2)
