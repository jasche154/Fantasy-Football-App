"""Replay League box scores: one skill game's passing, rushing and receiving line (K/DST have none).

`line` copies a GameLine, `total` sums lines (season totals, whole numbers) and `mean` averages them
(per-game rates, 1 decimal; None for no games).
"""
from collections.abc import Iterable
from dataclasses import astuple, dataclass, fields

from services.projections import GameLine


@dataclass(frozen=True)
class BoxLine:                    # whole numbers per game or season; mean() gives 1-decimal floats
    pass_yds: float = 0
    pass_td: float = 0
    pass_int: float = 0
    carries: float = 0
    rush_yds: float = 0
    rush_td: float = 0
    targets: float = 0
    rec: float = 0
    rec_yds: float = 0
    rec_td: float = 0


KEYS = tuple(spec.name for spec in fields(BoxLine))


def line(game: GameLine) -> BoxLine:
    return BoxLine(game.passing_yards, game.passing_tds, game.interceptions, game.carries, game.rushing_yards,
                   game.rushing_tds, game.targets, game.receptions, game.receiving_yards, game.receiving_tds)


def total(lines: Iterable[BoxLine]) -> BoxLine:
    sums = [0] * len(KEYS)
    for box in lines:
        sums = [a + b for a, b in zip(sums, astuple(box), strict=True)]
    return BoxLine(*sums)


def mean(lines: Iterable[BoxLine]) -> BoxLine | None:
    lines = list(lines)
    if not lines:
        return None
    return BoxLine(*(round(value / len(lines), 1) for value in astuple(total(lines))))
