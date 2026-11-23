"""Replay League defense ratings: how much each NFL defense gives up to a position, as known before a week.

`ratings(data)` solves this season's played games (schedule_strength._solve: opponent-adjusted, shrunk by
games played) from an as_of copy, so a week's ratings only use the weeks before it. Last season's factors
(`load_prior`, kept on SeasonData.defense_prior) are blended in at half strength and fade as games arrive:

    factor = solved + (prior - 1) x PRIOR_WEIGHT x PRIOR_GAMES / (games + PRIOR_GAMES)

DefenseRating {rank, of, tier, factor, allowed}: rank 1 gives up the most to the position, tier "easy" is
the top quarter by rank and "tough" the bottom quarter, allowed = the league's average points per game at
the position x factor. None for K/DST, an unknown team or a bye, or when nothing is known (week 1 without
last season).
"""
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from statistics import mean
from types import MappingProxyType
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from services.projections import load_player_seasons
from services.schedule_strength import _solve, load_schedule, team_position_points
from services.scoring import ScoringSettings, scoring_for

if TYPE_CHECKING:
    from services.replay_pool import SeasonData

POSITIONS = ("QB", "RB", "WR", "TE")
PRIOR_GAMES = 4          # games of this season that weigh as much as last season's half-strength prior
PRIOR_WEIGHT = 0.5
AVERAGE = "*"            # defense_prior key (AVERAGE, position): last season's points per team-game there

Points = Mapping[tuple[str, str, int, str], float]      # (offense, defense, week, position) -> points


def _solve_all(points: Points) -> tuple[dict[tuple[str, str], tuple[float, int]], dict[str, float]]:
    """({(defense, position): (factor, games)}, {position: average points per team-game})."""
    by_position: dict[str, dict[tuple[str, str, int], float]] = defaultdict(dict)
    for (offense, defense, week, position), value in points.items():
        by_position[position][(offense, defense, week)] = value
    solved, averages = {}, {}
    for position, games in by_position.items():
        factors, counts = _solve(games)
        solved.update({(d, position): (factor, counts[d]) for d, factor in factors.items()})
        averages[position] = mean(games.values())
    return solved, averages


def load_prior(session: Session, season: int, scoring: str | ScoringSettings) -> Mapping[tuple[str, str], float]:
    """(defense, position) -> last season's factor, plus (AVERAGE, position) -> its average points per
    team-game; empty when last season's stats or schedule aren't loaded. `scoring`: a preset or full rules."""
    rules = scoring_for(scoring) if isinstance(scoring, str) else scoring
    players = load_player_seasons(session, season - 1, rules)
    schedule = load_schedule(session, season - 1)
    if not players or not schedule.games:
        return {}
    solved, averages = _solve_all(team_position_points(players, schedule))
    return {**{key: round(factor, 4) for key, (factor, _) in solved.items()},
            **{(AVERAGE, position): round(value, 2) for position, value in averages.items()}}


@dataclass(frozen=True)
class Ratings:
    table: Mapping[tuple[str, str], dict]          # (defense, position) -> DefenseRating

    def rating(self, defense: str | None, position: str) -> dict | None:
        """{rank, of, tier, factor, allowed}, or None for K/DST, an unknown team or nothing known."""
        found = self.table.get((defense, position))
        return dict(found) if found else None


def _played_points(data: "SeasonData") -> Points:
    teams = {(pid, week): team for pid, games in data.team_by_week.items() for week, team in games}
    totals: dict[tuple[str, str, int, str], float] = defaultdict(float)
    for (pid, week), value in data.actual.items():
        player, offense = data.players.get(pid), teams.get((pid, week))
        defense = data.schedule.opponent(offense, week)
        if player is not None and player.position in POSITIONS and defense:
            totals[(offense, defense, week, player.position)] += value
    return totals


def _tier(rank: int, of: int) -> str:
    quarter = of // 4
    return "easy" if rank <= quarter else "tough" if rank > of - quarter else "neutral"


@lru_cache(maxsize=128)
def ratings(data: "SeasonData") -> Ratings:
    """Ratings from `data` (an as_of copy; reads only actual, team_by_week, schedule.opponent, defense_prior).
    Cached per copy (SeasonData hashes by identity)."""
    solved, averages = _solve_all(_played_points(data))
    prior = data.defense_prior
    table = {}
    for position in POSITIONS:
        average = averages.get(position) or prior.get((AVERAGE, position))
        defenses = {d for d, p in (*solved, *prior) if p == position and d != AVERAGE}
        if not average or not defenses:
            continue
        factors = {}
        for defense in defenses:
            factor, games = solved.get((defense, position), (1.0, 0))
            if (defense, position) in prior:
                factor += (prior[(defense, position)] - 1) * PRIOR_WEIGHT * PRIOR_GAMES / (games + PRIOR_GAMES)
            factors[defense] = factor
        order = sorted(factors, key=lambda d: (-factors[d], d))
        for rank, defense in enumerate(order, start=1):
            table[(defense, position)] = {"rank": rank, "of": len(order), "tier": _tier(rank, len(order)),
                                          "factor": round(factors[defense], 3),
                                          "allowed": round(average * factors[defense], 1)}
    return Ratings(MappingProxyType(table))


def basis(data: "SeasonData") -> dict:
    """What the ratings in `data` rest on: {weeks: played weeks this season, prior: last season blended in}."""
    return {"weeks": len({week for _, week in data.actual}), "prior": bool(data.defense_prior)}
