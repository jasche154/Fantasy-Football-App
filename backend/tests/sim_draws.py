"""Many draws of one simulated game, centred once (sim_game.play rebuilds and re-centres both sides every call)."""
from collections.abc import Iterator, Mapping

import numpy as np

from services import sim_centre, sim_draw, sim_game
from services.scoring import ScoringSettings


def centred_sides(world, home: str, away: str, target: Mapping[str, float] | None = None) -> tuple:
    """(home side, away side) as sim_game.play centres them for `target` (None: on their own means)."""
    sides = sim_centre.centred({home: sim_game._side(world, home, away, set(), {}),
                                away: sim_game._side(world, away, home, set(), {})}, target)
    return sides[home], sides[away]


def games(world, home: str, away: str, scoring: ScoringSettings, n: int, seed: int,
          target: Mapping[str, float] | None = None) -> Iterator[sim_game.GameResult]:
    """`n` draws of the game, each exactly what sim_game.play would draw on the same sides."""
    sides, rng = centred_sides(world, home, away, target), np.random.default_rng(seed)
    for _ in range(n):
        yield sim_draw.draw_game(sides, rng, scoring)
