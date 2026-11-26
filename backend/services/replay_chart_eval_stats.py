"""Replay chart eval (services/replay_chart_eval.py): the scored rows and the report's numbers. Pure."""
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

import numpy as np

from services import replacement_eval

MIN_CHART = 50.0               # population: healthy_chart at least this
BANDS = (800.0, 600.0, 400.0, 250.0, 100.0, MIN_CHART)     # chart bands, lower edges, best first
TIERS = (1, 7, 13, 25, 37)     # position-rank tiers (first rank of each) for the held-out residuals

@dataclass(frozen=True)
class Row:
    season: int
    week: int
    lineup: str
    player_id: int
    name: str
    position: str
    chart: float
    healthy_chart: float
    out: bool
    target: float
    rule: str                  # healthy | healthy_later_missed | out_back | out_season
    pos_rank: int = 0          # by chart within his position that week

def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """replacement_eval.spearman, None for fewer than 3 values or a side with no spread."""
    if len(xs) < 3 or np.std(xs) == 0 or np.std(ys) == 0:
        return None
    return replacement_eval.spearman(xs, ys)


def _mean_spearman(rows: Sequence[Row], keep: Callable[[Row], bool]) -> float | None:
    """Mean over (season, week) of the Spearman correlation among the kept rows."""
    groups: dict[tuple[int, int], list[Row]] = defaultdict(list)
    for row in rows:
        if keep(row):
            groups[(row.season, row.week)].append(row)
    found = [r for r in (spearman([x.chart for x in g], [x.target for x in g]) for g in groups.values())
             if r is not None]
    return round(float(np.mean(found)), 3) if found else None


def band(chart: float) -> str:
    low = next((edge for edge in BANDS if chart >= edge), 0.0)
    above = [edge for edge in BANDS if edge > low]
    return f"{low:.0f}+" if not above else f"{low:.0f}-{min(above):.0f}"


def calibration(rows: Iterable[Row]) -> dict[str, dict[str, tuple[float, int]]]:
    """{band: {position | "ALL": (mean target, players)}}."""
    cells: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        cells[band(row.chart)][row.position].append(row.target)
        cells[band(row.chart)]["ALL"].append(row.target)
    return {b: {p: (round(float(np.mean(v)), 2), len(v)) for p, v in sorted(by.items())}
            for b, by in sorted(cells.items(), key=lambda item: -float(item[0].split("+")[0].split("-")[0]))}


def held_out_error(rows: Sequence[Row], scored: Callable[[Row], bool] = lambda row: True) -> dict:
    """Leave-season-out: per held-out season, a position-blind curve (mean target per chart band) fitted on
    the other seasons; RMSE of the held-out targets against it, and each position's (and position-rank
    tier's) mean miss (bias). `scored`: which held-out rows are scored (the curve always uses every row)."""
    seasons = sorted({r.season for r in rows})
    errors: list[float] = []
    bias: dict[str, list[float]] = defaultdict(list)
    tiers: dict[str, list[float]] = defaultdict(list)
    for held in seasons:
        curve: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            if row.season != held:
                curve[band(row.chart)].append(row.target)
        means = {b: float(np.mean(v)) for b, v in curve.items()}
        for row in rows:
            if row.season == held and band(row.chart) in means and scored(row):
                miss = row.target - means[band(row.chart)]
                errors.append(miss * miss)
                bias[row.position].append(miss)
                tiers[f"{row.position}{tier(row.pos_rank)}"].append(miss)
    if not errors:
        return {}
    return {"rmse": round(float(np.sqrt(np.mean(errors))), 3),
            **{f"bias_{p}": round(float(np.mean(v)), 3) for p, v in sorted(bias.items())},
            "tiers": {name: (round(float(np.mean(v)), 2), len(v)) for name, v in sorted(tiers.items())}}


def tier(pos_rank: int) -> str:
    first = max(t for t in TIERS if t <= max(1, pos_rank))
    later = [t for t in TIERS if t > first]
    return f"{first}-{later[0] - 1}" if later else f"{first}+"


def realised_share(rows: Sequence[Row]) -> float | None:
    """Out players' targets over what healthy players of the same healthy_chart band produced: the share of
    his value the known absence really cost him (compare with their mean chart / healthy_chart)."""
    healthy: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if not row.out:
            healthy[band(row.healthy_chart)].append(row.target)
    means = {b: float(np.mean(v)) for b, v in healthy.items()}
    out = [(row.target, means[band(row.healthy_chart)]) for row in rows
           if row.out and band(row.healthy_chart) in means]
    expected = sum(e for _, e in out)
    return round(sum(t for t, _ in out) / expected, 3) if out and expected > 0 else None


def evaluate(rows: Sequence[Row]) -> dict:
    """The report for one lineup's rows."""
    positions = sorted({r.position for r in rows})
    out_rows = [r for r in rows if r.out]
    return {
        "players": len(rows),
        "spearman": _mean_spearman(rows, lambda r: True),
        "spearman_by_position": {p: _mean_spearman(rows, lambda r, p=p: r.position == p) for p in positions},
        "held_out": held_out_error(rows),
        "calibration": calibration(rows),
        "out": {"players": len(out_rows), "spearman": _mean_spearman(out_rows, lambda r: True),
                "held_out": held_out_error(rows, lambda r: r.out), "realised_share": realised_share(rows),
                "calibration": calibration(out_rows),
                "chart_share": round(float(np.mean([r.chart / r.healthy_chart for r in out_rows])), 3)
                if out_rows else None},
    }
