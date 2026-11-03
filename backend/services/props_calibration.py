"""Grade the stats implied by Vegas props against what happened, and learn a per-stat correction.

Run from backend/ (reads only; never calls The Odds API):
    python -m services.props_calibration --season 2026

Each week's stored props (weekly_source_projections, source "vegas_props") are expected
stats: pass_yds, pass_tds, ints, rush_yds, rec, rec_yds and scrimmage_tds. Paired with the
player's box score that week (players without one didn't play, and their props were void),
each stat gets a multiplicative factor:

    raw    = sum(actual) / sum(prop expectation)       (matches the mean)
    factor = 1 + n / (n + PSEUDO_SAMPLES) * (raw - 1), within [MIN_FACTOR, MAX_FACTOR]

and 1.0 with fewer than MIN_SAMPLES pairs. Point in time, like services/weight_learning.py:
the factors for week W only use weeks before W.

ENABLED decides whether services/weekly_rankings.py applies the factors where props become
points. It stays off until the correction lowers the out-of-sample error of prop-implied
fantasy points (the report below; README "Weekly projections" has the latest numbers).
"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse  # noqa: E402
import json
import logging
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from database.models import PlayerStatsWeekly, WeeklySourceProjection
from services.league import LeagueSettings
from services.weekly_model import points_from_stats

log = logging.getLogger(__name__)

SOURCE = "vegas_props"            # integrations.odds_api.SOURCE (kept literal: no network module import)
ENABLED = False                   # see the module docstring; flip only when the report shows a gain
STATS = ("pass_yds", "pass_tds", "ints", "rush_yds", "rec", "rec_yds", "scrimmage_tds")
MIN_SAMPLES = 30                  # pairs per stat before any correction
PSEUDO_SAMPLES = 150              # how many pairs the "no correction" prior counts as
MIN_FACTOR, MAX_FACTOR = 0.8, 1.2


@dataclass(frozen=True)
class Pair:
    season: int
    week: int
    player_id: int
    position: str | None
    props: Mapping[str, float]
    actual: Mapping[str, float]


def actual_stats(row) -> dict[str, float]:
    """A box score in the props' stat keys."""
    value = lambda x: float(x or 0)  # noqa: E731
    return {"pass_yds": value(row.passing_yards), "pass_tds": value(row.passing_tds),
            "ints": value(row.passing_interceptions), "rush_yds": value(row.rushing_yards),
            "rec": value(row.receptions), "rec_yds": value(row.receiving_yards),
            "scrimmage_tds": value(row.rushing_tds) + value(row.receiving_tds)}


def load_pairs(session: Session, season: int, before_week: int | None = None) -> list[Pair]:
    """Stored props with the box score that week: this season (before before_week) and earlier seasons."""
    this_season = WeeklySourceProjection.season == season
    if before_week is not None:
        this_season = and_(this_season, WeeklySourceProjection.week < before_week)
    when = or_(WeeklySourceProjection.season < season, this_season)
    latest: dict[tuple[int, int, int], WeeklySourceProjection] = {}
    for row in session.scalars(select(WeeklySourceProjection).where(
            WeeklySourceProjection.source == SOURCE, WeeklySourceProjection.player_id.is_not(None), when)
            .order_by(WeeklySourceProjection.fetched_at)):
        latest[(row.season, row.week, row.player_id)] = row          # the last snapshot before the game
    if not latest:
        return []
    seasons = {s for s, _, _ in latest}
    box = {(r.season, r.week, r.player_id): r for r in session.scalars(select(PlayerStatsWeekly).where(
        PlayerStatsWeekly.season.in_(seasons), PlayerStatsWeekly.season_type == "REG",
        PlayerStatsWeekly.player_id.in_({pid for _, _, pid in latest})))}
    pairs = []
    for key, row in sorted(latest.items()):
        if key not in box:
            continue                                     # didn't play: the props were void
        try:
            props = {k: float(v) for k, v in json.loads(row.stats or "{}").items() if k in STATS}
        except (ValueError, TypeError, AttributeError):
            continue
        if props:
            pairs.append(Pair(*key, row.position, props, actual_stats(box[key])))
    return pairs


def fit(pairs: Sequence[Pair]) -> dict[str, float]:
    """{stat: factor}, shrunk toward 1.0 and 1.0 below MIN_SAMPLES (stats without pairs are left out)."""
    sums: dict[str, list[float]] = defaultdict(lambda: [0, 0.0, 0.0])
    for pair in pairs:
        for stat, expected in pair.props.items():
            entry = sums[stat]
            entry[0] += 1
            entry[1] += expected
            entry[2] += pair.actual.get(stat, 0.0)
    factors = {}
    for stat, (n, expected, actual) in sums.items():
        if n < MIN_SAMPLES or expected <= 0:
            factors[stat] = 1.0
            continue
        raw = actual / expected
        factor = 1.0 + n / (n + PSEUDO_SAMPLES) * (raw - 1.0)
        factors[stat] = round(min(MAX_FACTOR, max(MIN_FACTOR, factor)), 4)
    return factors


def factors_for(session: Session, season: int, week: int) -> dict[str, float]:
    """The factors the week's rankings apply: {} (no correction) unless ENABLED."""
    return fit(load_pairs(session, season, before_week=week)) if ENABLED else {}


def corrected(props: Mapping[str, float], factors: Mapping[str, float]) -> dict[str, float]:
    """Props stats times their factors (stats without a factor unchanged)."""
    return {stat: value * factors.get(stat, 1.0) for stat, value in props.items()}


def _rmse(errors: Sequence[float]) -> float | None:
    return round(math.sqrt(sum(e * e for e in errors) / len(errors)), 3) if errors else None


def evaluate(pairs: Sequence[Pair], league: LeagueSettings = LeagueSettings()) -> dict:
    """Out-of-sample error of prop-implied points (on the stats props cover), with and without the correction.

    rolling: each week corrected with factors from earlier weeks only (what the app would do).
    leave_one_week_out: each week corrected with factors from every other week (more test
    weeks when only a few are stored; not point in time, so a secondary check).
    """
    scoring = league.scoring_settings()
    weeks = sorted({(p.season, p.week) for p in pairs})
    out = {}
    for name, train_of in (("rolling", lambda w: [p for p in pairs if (p.season, p.week) < w]),
                           ("leave_one_week_out", lambda w: [p for p in pairs if (p.season, p.week) != w])):
        raw_errors, fixed_errors, by_week = [], [], []
        for week in weeks:
            factors = fit(train_of(week))
            test = [p for p in pairs if (p.season, p.week) == week]
            if not any(f != 1.0 for f in factors.values()):
                continue                                 # nothing learned yet: same as uncorrected
            raw, fixed = [], []
            for p in test:
                actual = points_from_stats({k: p.actual[k] for k in p.props}, p.position, scoring)
                raw.append(points_from_stats(p.props, p.position, scoring) - actual)
                fixed.append(points_from_stats(corrected(p.props, factors), p.position, scoring) - actual)
            raw_errors += raw
            fixed_errors += fixed
            by_week.append({"season": week[0], "week": week[1], "n": len(test), "factors": factors,
                            "rmse_raw": _rmse(raw), "rmse_corrected": _rmse(fixed)})
        out[name] = {"n": len(raw_errors), "rmse_raw": _rmse(raw_errors),
                     "rmse_corrected": _rmse(fixed_errors), "weeks": by_week}
    misses: dict[str, list[float]] = defaultdict(list)       # actual minus expected, per stat
    for p in pairs:
        for stat, expected in p.props.items():
            misses[stat].append(p.actual.get(stat, 0.0) - expected)
    out["bias"] = {stat: {"n": len(m), "mean_miss": round(sum(m) / len(m), 3)} for stat, m in sorted(misses.items())}
    out["pairs"] = len(pairs)
    out["factors_all_weeks"] = fit(pairs)
    return out


def main(argv: list[str] | None = None) -> None:
    from database.db import engine

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--scoring", choices=("ppr", "half", "std"), default="ppr")
    args = parser.parse_args(argv)
    with Session(engine) as session:
        pairs = load_pairs(session, args.season, before_week=None)
    if not pairs:
        log.info("Not evaluated: no stored props with box scores.")
        return
    log.info(json.dumps(evaluate(pairs, LeagueSettings(scoring=args.scoring)), indent=2))


if __name__ == "__main__":
    main()
