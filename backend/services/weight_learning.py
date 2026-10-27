"""Learn the Week X source weights from how each source actually did, conservatively.

Run from backend/ (populate_stats also runs it after each load):
    python -m services.weight_learning --season 2026            # learn from new completed weeks
    python -m services.weight_learning --season 2026 --dry-run  # show what it would change

For each completed week that has pre-kickoff projections in projection_history (and hasn't
been learned from yet), in order:
1. Take every player who played, with each source's points and his actual points.
2. Fit the weights whose blend (re-balanced over the sources a player has, as the rankings
   do) would have had the smallest squared error that week ("optimal").
3. Move the current weights only part of the way there:
       step = trust × (optimal − current), trust = rows / (rows + PRIOR_ROWS)
   then cap each source's move at MAX_STEP, keep every weight at least MIN_WEIGHT, and
   re-normalize. One week of ~350 players moves each weight by at most a point or two;
   a source has to beat the others week after week to gain real weight.

The defaults in weekly_rankings.WEIGHTS are the starting point (the prior). Each week's
update is stored in learned_weights (local database), and the newest one is what the
rankings use. Delete those rows to go back to the defaults.
"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse  # noqa: E402
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import mean

import numpy as np
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database.models import Base, LearnedWeights, ProjectionHistory
from services.projection_eval import actual_points
from services.schedule_strength import load_schedule

log = logging.getLogger(__name__)

DEFAULT_WEIGHTS = {"vegas_props": 0.35, "josh_hayden": 0.30, "model": 0.15, "yahoo_consensus": 0.10, "espn": 0.10}
PRIOR_ROWS = 3000          # player-weeks of evidence the default weights are worth (~8 weeks)
MAX_STEP = 0.03            # the most any one weight moves in a week
MIN_WEIGHT = 0.05          # no source is ever dropped entirely
MIN_ROWS = 100             # fewer scored player-weeks than this: too noisy to learn from
SCORING_PREFERENCE = ("ppr", "half", "std")    # one row per player-week, in this order
FIT_STEPS, FIT_RATE = 3000, 0.5


@dataclass(frozen=True)
class Sample:
    sources: Mapping[str, float]
    actual: float


@dataclass(frozen=True)
class Update:
    season: int
    week: int
    rows: int
    before: dict[str, float]
    optimal: dict[str, float]
    after: dict[str, float]
    mae_before: float
    mae_after: float


def blend(weights: Mapping[str, float], sources: Mapping[str, float]) -> float | None:
    total = sum(weights.get(k, 0.0) for k in sources)
    return sum(weights.get(k, 0.0) * v for k, v in sources.items()) / total if total else None


def mae(weights: Mapping[str, float], samples: Sequence[Sample]) -> float:
    errors = [abs(b - s.actual) for s in samples if (b := blend(weights, s.sources)) is not None]
    return round(mean(errors), 3) if errors else 0.0


def fit_weights(samples: Sequence[Sample], keys: Sequence[str]) -> dict[str, float]:
    """Weights on the simplex minimizing squared error of the re-balanced blend (exponentiated gradient)."""
    index = {k: i for i, k in enumerate(keys)}
    x = np.zeros((len(samples), len(keys)))
    mask = np.zeros_like(x)
    for row, sample in enumerate(samples):
        for key, value in sample.sources.items():
            if key in index:
                x[row, index[key]] = value
                mask[row, index[key]] = 1.0
    y = np.array([s.actual for s in samples])
    w = np.full(len(keys), 1.0 / len(keys))
    for _ in range(FIT_STEPS):
        total = mask @ w
        pred = (x * w).sum(axis=1) / total
        residual = (pred - y) / max(len(y), 1)
        # d pred / d w_k = mask_k (x_k - pred) / total
        grad = ((residual / total)[:, None] * mask * (x - pred[:, None])).sum(axis=0)
        w = w * np.exp(-FIT_RATE * grad / (np.abs(grad).max() + 1e-12))
        w = w / w.sum()
    return {k: round(float(w[index[k]]), 4) for k in keys}


def conservative_step(current: Mapping[str, float], optimal: Mapping[str, float], rows: int) -> dict[str, float]:
    trust = rows / (rows + PRIOR_ROWS)
    moved = {k: current[k] + max(-MAX_STEP, min(MAX_STEP, trust * (optimal.get(k, current[k]) - current[k])))
             for k in current}
    return _normalize_with_floor(moved)


def _normalize_with_floor(weights: Mapping[str, float]) -> dict[str, float]:
    """Scale to sum to 1 with every weight at least MIN_WEIGHT (floored ones stay at the floor)."""
    floored: set[str] = set()
    while True:
        free = {k: max(v, 0.0) for k, v in weights.items() if k not in floored}
        room = 1.0 - MIN_WEIGHT * len(floored)
        total = sum(free.values()) or 1.0
        scaled = {k: v / total * room for k, v in free.items()}
        low = {k for k, v in scaled.items() if v < MIN_WEIGHT}
        if not low:
            return {**{k: MIN_WEIGHT for k in floored}, **{k: round(v, 4) for k, v in scaled.items()}}
        floored |= low


def _ensure_table(session: Session) -> None:
    Base.metadata.create_all(session.get_bind(), tables=[LearnedWeights.__table__])


def effective_weights(session: Session, season: int | None = None,
                      before_week: int | None = None) -> tuple[dict[str, float], LearnedWeights | None]:
    """The newest learned weights, or the defaults when nothing has been learned (or can't be read).

    With season and before_week, only updates learned from earlier weeks count, so a past
    week is shown as it was projected rather than with hindsight.
    """
    try:
        _ensure_table(session)
        query = select(LearnedWeights)
        if season is not None and before_week is not None:
            query = query.where((LearnedWeights.season < season)
                                | ((LearnedWeights.season == season) & (LearnedWeights.week < before_week)))
        latest = session.scalar(query.order_by(LearnedWeights.season.desc(), LearnedWeights.week.desc()).limit(1))
    except SQLAlchemyError:
        log.warning("Could not read learned weights; using the defaults", exc_info=True)
        session.rollback()
        return dict(DEFAULT_WEIGHTS), None
    if latest is None:
        return dict(DEFAULT_WEIGHTS), None
    learned = json.loads(latest.weights)
    return {k: learned.get(k, v) for k, v in DEFAULT_WEIGHTS.items()}, latest


def samples_for_week(session: Session, season: int, week: int) -> list[Sample]:
    """Pre-kickoff source points and actual points for every player who played that week."""
    rows = session.scalars(select(ProjectionHistory).where(
        ProjectionHistory.season == season, ProjectionHistory.week == week)).all()
    chosen: dict[int, ProjectionHistory] = {}
    for row in sorted(rows, key=lambda r: SCORING_PREFERENCE.index(r.scoring) if r.scoring in SCORING_PREFERENCE else 9):
        chosen.setdefault(row.player_id, row)
    actuals = {scoring: actual_points(session, season, scoring) for scoring in {r.scoring for r in chosen.values()}}
    samples = []
    for row in chosen.values():
        actual = actuals[row.scoring].get((row.player_id, week))
        sources = {k: v for k, v in json.loads(row.sources).items() if k in DEFAULT_WEIGHTS}
        if actual is not None and sources:
            samples.append(Sample(sources, actual))
    return samples


def _week_complete(session: Session, season: int, week: int) -> bool:
    games = [g for g in load_schedule(session, season).games if g.week == week]
    return bool(games) and all(g.is_final for g in games)


def learn(session: Session, season: int, apply: bool = True) -> list[Update]:
    """Update the weights from every completed, not-yet-learned week with enough history."""
    _ensure_table(session)
    learned = set(session.scalars(select(LearnedWeights.week).where(LearnedWeights.season == season)))
    weeks = sorted(set(session.scalars(select(ProjectionHistory.week).where(ProjectionHistory.season == season))))
    current, _ = effective_weights(session)
    updates = []
    for week in weeks:
        if week in learned or not _week_complete(session, season, week):
            continue
        samples = samples_for_week(session, season, week)
        if len(samples) < MIN_ROWS:
            log.info("Week %s: %s scored player-weeks, too few to learn from", week, len(samples))
            continue
        optimal = fit_weights(samples, list(DEFAULT_WEIGHTS))
        after = conservative_step(current, optimal, len(samples))
        update = Update(season, week, len(samples), dict(current), optimal, after,
                        mae(current, samples), mae(after, samples))
        updates.append(update)
        if apply:
            session.add(LearnedWeights(season=season, week=week, rows=len(samples), weights=json.dumps(after),
                                       optimal=json.dumps(optimal), mae_before=update.mae_before,
                                       mae_after=update.mae_after))
            session.commit()
        current = after
    return updates


def describe(update: Update) -> str:
    lines = [f"Week {update.week}: {update.rows} player-weeks, blend MAE {update.mae_before} -> {update.mae_after}"]
    for key in DEFAULT_WEIGHTS:
        lines.append(f"  {key:16} {update.before[key]:.3f} -> {update.after[key]:.3f}   (best fit this week {update.optimal[key]:.3f})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    from database.db import engine

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--dry-run", action="store_true", help="show the update without saving it")
    args = parser.parse_args(argv)
    with Session(engine) as session:
        updates = learn(session, args.season, apply=not args.dry_run)
    if not updates:
        log.info("Nothing new to learn from (needs a completed week with pre-kickoff projections).")
    for update in updates:
        log.info(describe(update))


if __name__ == "__main__":
    main()
