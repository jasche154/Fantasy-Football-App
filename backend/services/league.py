"""League settings and positional replacement levels."""
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from services.scoring import ScoringSettings, scoring_for

POSITIONS: tuple[str, ...] = ("QB", "RB", "WR", "TE")
FLEX_POSITIONS: tuple[str, ...] = ("RB", "WR", "TE")
SUPERFLEX_POSITIONS: tuple[str, ...] = ("QB", "RB", "WR", "TE")
DEFAULT_STARTERS = MappingProxyType({"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1})


@dataclass(frozen=True)
class LeagueSettings:
    scoring: str = "ppr"
    teams: int = 12
    superflex: bool = False
    te_premium: float = 0.0
    # Excluded from the hash (mappings aren't hashable) but still compared for equality.
    starters: Mapping[str, int] = field(default_factory=lambda: DEFAULT_STARTERS, hash=False)
    # The league's fantasy-playoff weeks (from Yahoo); None = the last 3 weeks of the season.
    playoff_weeks: tuple[int, ...] | None = None
    bench: int = 6

    def scoring_settings(self) -> ScoringSettings:
        return scoring_for(self.scoring, self.te_premium)


def _take_best(pools: dict[str, list[float]], taken: dict[str, int], eligible: Iterable[str],
               slots: int) -> dict[str, int]:
    """Greedily fill `slots` flex spots with the best remaining eligible players."""
    counts = dict(taken)
    for _ in range(slots):
        best_position, best_value = None, -math.inf
        for position in eligible:
            index = counts[position]
            if index < len(pools[position]) and pools[position][index] > best_value:
                best_position, best_value = position, pools[position][index]
        if best_position is None:
            break
        counts[best_position] += 1
    return counts


def starter_counts(pools: dict[str, list[float]], league: LeagueSettings) -> dict[str, int]:
    """League-wide number of starters at each position, flex spots included."""
    counts = {p: min(len(pools[p]), league.teams * league.starters.get(p, 0)) for p in POSITIONS}
    counts = _take_best(pools, counts, FLEX_POSITIONS, league.teams * league.starters.get("FLEX", 0))
    if league.superflex:
        counts = _take_best(pools, counts, SUPERFLEX_POSITIONS, league.teams)
    return counts


def replacement_levels(projections: Iterable[tuple[str, float]], league: LeagueSettings) -> dict[str, float]:
    """Points per game of the best player each position would find on waivers.

    Rostered players = starters plus each position's share of the league's bench spots,
    split in proportion to how many starters the position has.
    """
    pools: dict[str, list[float]] = {p: [] for p in POSITIONS}
    for position, ppg in projections:
        if position in pools:
            pools[position].append(ppg)
    for position in POSITIONS:
        pools[position].sort(reverse=True)

    starters = starter_counts(pools, league)
    total_starters = sum(starters.values()) or 1
    bench_spots = league.teams * league.bench
    levels = {}
    for position in POSITIONS:
        rostered = starters[position] + round(bench_spots * starters[position] / total_starters)
        pool = pools[position]
        levels[position] = pool[rostered] if rostered < len(pool) else 0.0
    return levels
