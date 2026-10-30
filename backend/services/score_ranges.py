"""How a player's score is spread around his projection: empirical quantiles, busts included.

Fit (run from backend/, takes a few minutes):
    python -m services.score_ranges --seasons 2021-2025

For each position and projection tier, the 5th/10th/25th/50th/75th/90th/95th percentile of
    actual points / if-active projection
over every game the player was active, low-snap games and zeros included (the old ranges
only used games with 50%+ snaps, so 25% of players landed below their "10th percentile").
The model's projections come from services/backtest.py (point in time, no look-ahead). The
blend misses by about 5% less than the model, so each quantile's distance from the median
is scaled by BLEND_SHRINK.

Shape: running backs and pass catchers whose points come mostly from touchdowns bust more
often; deep targets (high aDOT) carry more ceiling and less floor. Both are measured over the
player's last four games and adjust the low quantiles (5th-25th) and high ones (75th-95th).

Kickers and defenses keep their own spread (services/kdst_rankings.py); for them the
quantiles are a normal approximation of their floor and ceiling.
"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse  # noqa: E402
import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from functools import lru_cache

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import PlayerStatsWeekly

log = logging.getLogger(__name__)

PATH = Path(__file__).with_name("score_ranges.json")
LEVELS = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)
FLOOR_INDEX, MEDIAN_INDEX, CEILING_INDEX = 1, 3, 5
TIER_EDGES = (0.0, 4.0, 8.0, 12.0, 16.0, float("inf"))
MIN_TIER_GAMES = 60
BLEND_SHRINK = 0.95
SHAPE_GAMES = 4
MIN_SHAPE_TARGETS = 8
# (low cut, high cut) and the (floor factor, ceiling factor) for the low and high bands; the middle band is 1.0.
TD_SHARE_BANDS = ((0.14, 0.25), (1.03, 0.97), (0.85, 0.99))
ADOT_BANDS = ((5.4, 10.1), (1.14, 0.93), (0.83, 1.03))
SHAPED_POSITIONS = ("RB", "WR", "TE")
# The backtest only projects players with a 5+ point rate, so projections under 4 points have no
# fitted tier; these are the blend's own 2026 quantiles for that tier (all positions).
LOW_TIER = {"min": 0.0, "max": 4.0, "q": [0.0, 0.0, 0.0, 0.0, 1.41, 3.22, 4.42]}
Z = {0.05: -1.645, 0.10: -1.2816, 0.25: -0.6745, 0.50: 0.0, 0.75: 0.6745, 0.90: 1.2816, 0.95: 1.645}


def fit(ratios_by_position: Mapping[str, Sequence[tuple[float, float]]]) -> dict:
    """{position: {"all": quantiles, "tiers": [{min, max, q, n}]}} from (if_active, ratio) pairs."""
    out = {}
    for position, pairs in ratios_by_position.items():
        if not pairs:
            continue
        def quantiles(values: Sequence[float]) -> list[float]:
            q = np.percentile(values, [100 * level for level in LEVELS])
            median = q[MEDIAN_INDEX]
            return [0.0 if v <= 0 else round(max(0.0, median + BLEND_SHRINK * (v - median)), 3) for v in q]

        tiers = []
        for low, high in zip(TIER_EDGES, TIER_EDGES[1:]):
            ratios = [r for projection, r in pairs if low <= projection < high]
            if len(ratios) >= MIN_TIER_GAMES:
                tiers.append({"min": low, "max": None if high == float("inf") else high, "q": quantiles(ratios),
                              "n": len(ratios)})
        out[position] = {"all": quantiles([r for _, r in pairs]), "n": len(pairs), "tiers": tiers}
    return out


@lru_cache(maxsize=1)
def stored() -> dict:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))["positions"]
    except (OSError, ValueError, KeyError):
        return {}


def ratios_for(position: str, if_active: float) -> list[float] | None:
    ranges = stored().get(position)
    if not ranges:
        return None
    for tier in ranges["tiers"]:
        if tier["min"] <= if_active < (tier["max"] if tier["max"] is not None else float("inf")):
            return list(tier["q"])
    if if_active < LOW_TIER["max"]:
        return list(LOW_TIER["q"])
    return list(ranges["all"])


def _band(value: float | None, bands: tuple) -> tuple[float, float]:
    (low_cut, high_cut), low, high = bands
    if value is None:
        return 1.0, 1.0
    return low if value < low_cut else high if value > high_cut else (1.0, 1.0)


def shape_factors(position: str, td_share: float | None, adot: float | None) -> tuple[float, float]:
    """(floor factor, ceiling factor) from the player's touchdown dependence and target depth."""
    if position not in SHAPED_POSITIONS:
        return 1.0, 1.0
    td_floor, td_ceiling = _band(td_share, TD_SHARE_BANDS)
    adot_floor, adot_ceiling = _band(adot, ADOT_BANDS)
    return td_floor * adot_floor, td_ceiling * adot_ceiling


def player_quantiles(position: str, if_active: float, td_share: float | None = None,
                     adot: float | None = None) -> tuple[float, ...] | None:
    """Points at each of LEVELS for this projection (if he plays), or None for unmodeled positions."""
    ratios = ratios_for(position, if_active)
    if ratios is None or if_active <= 0:
        return None
    floor_factor, ceiling_factor = shape_factors(position, td_share, adot)
    shaped = [r * floor_factor if i < MEDIAN_INDEX else r * ceiling_factor if i > MEDIAN_INDEX else r
              for i, r in enumerate(ratios)]
    return tuple(round(if_active * r, 2) for r in sorted(shaped))


def normal_quantiles(mean: float, floor: float, ceiling: float) -> tuple[float, ...]:
    """Quantiles of a normal whose 10th/90th percentiles are floor/ceiling (kickers and defenses)."""
    sd = max(0.5, (ceiling - floor) / (2 * Z[0.90]))
    return tuple(round(mean + Z[level] * sd, 2) for level in LEVELS)


def sample(quantiles: Sequence[float], u: np.ndarray) -> np.ndarray:
    """Scores at uniform draws u, interpolating between the quantiles (tails run to 0 and past the 95th)."""
    q = list(quantiles)
    low_end = max(0.0, q[0] - (q[1] - q[0])) if q[0] >= 0 else q[0] - (q[1] - q[0])
    high_end = q[-1] + 2.0 * (q[-1] - q[-2])
    return np.interp(u, (0.0, *LEVELS, 1.0), (low_end, *q, high_end))


def shape_inputs(session: Session, season: int, player_ids: Iterable[int],
                 before_week: int | None = None) -> dict[int, tuple[float | None, float | None]]:
    """{player_id: (td_share, adot)} over each player's last SHAPE_GAMES games (before before_week)."""
    query = select(PlayerStatsWeekly.player_id, PlayerStatsWeekly.week, PlayerStatsWeekly.fantasy_points,
                   PlayerStatsWeekly.receptions, PlayerStatsWeekly.rushing_tds, PlayerStatsWeekly.receiving_tds,
                   PlayerStatsWeekly.targets, PlayerStatsWeekly.receiving_air_yards).where(
        PlayerStatsWeekly.season == season, PlayerStatsWeekly.season_type == "REG",
        PlayerStatsWeekly.player_id.in_(list(player_ids)))
    if before_week is not None:
        query = query.where(PlayerStatsWeekly.week < before_week)
    games: dict[int, list] = {}
    for row in session.execute(query.order_by(PlayerStatsWeekly.week)):
        games.setdefault(row.player_id, []).append(row)
    out = {}
    for pid, rows in games.items():
        recent = rows[-SHAPE_GAMES:]
        points = sum((r.fantasy_points or 0.0) + (r.receptions or 0) for r in recent)
        tds = sum((r.rushing_tds or 0) + (r.receiving_tds or 0) for r in recent)
        targets = sum(r.targets or 0 for r in recent)
        air = sum(r.receiving_air_yards or 0 for r in recent)
        out[pid] = (6 * tds / points if points > 0 else None, air / targets if targets >= MIN_SHAPE_TARGETS else None)
    return out


def coverage(rows: Iterable[tuple[str, float, float]]) -> dict:
    """Share of (position, if_active, actual) below the 10th and above the 90th percentile."""
    below = above = n = 0
    for position, if_active, actual in rows:
        q = player_quantiles(position, if_active)
        if q is None:
            continue
        n += 1
        below += actual < q[FLOOR_INDEX]
        above += actual > q[CEILING_INDEX]
    return {"n": n, "below_floor": round(below / n, 3) if n else None, "above_ceiling": round(above / n, 3) if n else None}


def _seasons(text: str) -> list[int]:
    first, _, last = text.partition("-")
    return list(range(int(first), int(last or first) + 1))


def main(argv: list[str] | None = None) -> None:
    from database.db import engine
    from services.backtest import load_season, run
    from services.weekly_model import WeeklyModelSettings

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seasons", default="2021-2025")
    args = parser.parse_args(argv)
    pairs: dict[str, list[tuple[float, float]]] = {}
    with Session(engine) as session:
        for season in _seasons(args.seasons):
            targets = run(session, load_season(session, season), WeeklyModelSettings())
            for t in targets:
                if t.played and t.if_active > 0:          # every active game, zeros and low snaps included
                    pairs.setdefault(t.position, []).append((t.if_active, t.actual / t.if_active))
            log.info("%s: %d games", season, len(targets))
    positions = fit(pairs)
    PATH.write_text(json.dumps({"seasons": args.seasons, "levels": LEVELS, "blend_shrink": BLEND_SHRINK,
                                "positions": positions}, indent=2), encoding="utf-8")
    stored.cache_clear()
    log.info("Wrote %s", PATH)


if __name__ == "__main__":
    main()
