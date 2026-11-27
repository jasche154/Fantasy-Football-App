"""Replay League box scores: one skill game's passing, rushing and receiving line (K/DST have none).

`line` copies a GameLine, `total` sums lines (season totals, whole numbers) and `mean` averages them
(per-game rates, 1 decimal; None for no games). `shown` is a line as the views send it: completions and attempts
null when the line never recorded them (a passing line with no attempts: a simulated league saved before they
were kept, or a database row without them).
"""
from collections.abc import Iterable
from dataclasses import asdict, dataclass, fields

from services.projections import GameLine


@dataclass(frozen=True)
class BoxLine:                    # whole numbers per game or season; mean() gives 1-decimal floats
    pass_cmp: float = 0           # completions and attempts (sacks are not attempts), first as on a box score
    pass_att: float = 0
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
    return BoxLine(pass_cmp=game.completions, pass_att=game.attempts, pass_yds=game.passing_yards,
                   pass_td=game.passing_tds, pass_int=game.interceptions, carries=game.carries,
                   rush_yds=game.rushing_yards, rush_td=game.rushing_tds, targets=game.targets,
                   rec=game.receptions, rec_yds=game.receiving_yards, rec_td=game.receiving_tds)


def total(lines: Iterable[BoxLine]) -> BoxLine:
    sums = dict.fromkeys(KEYS, 0)
    for box in lines:
        for key in KEYS:
            sums[key] += getattr(box, key)
    return BoxLine(**sums)


def mean(lines: Iterable[BoxLine]) -> BoxLine | None:
    lines = list(lines)
    if not lines:
        return None
    summed = total(lines)
    return BoxLine(**{key: round(getattr(summed, key) / len(lines), 1) for key in KEYS})


def recorded(box: BoxLine) -> bool:
    """False for a passing line without attempts: its completions and attempts were never recorded."""
    return bool(box.pass_att) or not (box.pass_yds or box.pass_td or box.pass_int)


def shown(box: BoxLine) -> dict:
    """The line as a view sends it (`asdict`, with pass_cmp / pass_att null when not `recorded`)."""
    out = asdict(box)
    if not recorded(box):
        out["pass_cmp"] = out["pass_att"] = None
    return out
