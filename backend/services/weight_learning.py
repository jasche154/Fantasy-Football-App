"""Learn the Week X source weights from how each source actually did, pooled and shrunk.

Run from backend/ (populate_stats also runs it after each load):
    python -m services.weight_learning --season 2026            # learn from new completed weeks
    python -m services.weight_learning --season 2026 --dry-run  # show what it would change

Sources are blended in four groups: Vegas props, the experts (Josh & Hayden and Yahoo's
consensus, averaged as points: they are correlated Yahoo opinions), ESPN and the app's model.

After each completed week, the weights are refit on every week so far (rolling origin: a
week's weights only ever use earlier weeks):
1. Rows: pre-kickoff projections of fantasy-relevant players (the top 36 at each position
   that week, top 24 at QB) who played, with each group's points and the actual points.
   A group missing from a row is filled with the mean of the groups present, matching how
   the rankings re-balance over the sources a player has.
2. Fit: least squares shrunk toward the prior (DEFAULT_WEIGHTS) as if the prior were backed
   by PSEUDO_ROWS rows. The sources are 0.9+ correlated, so an unshrunk fit swings wildly.
3. Limits: no negative weights, props at least PROPS_FLOOR (their value is late news, which
   the history can't show yet), every other group at least MIN_WEIGHT, then normalize.
With fewer than MIN_POOLED_ROWS rows the prior is used unchanged.

Each week's result is stored in learned_weights (local database); the newest one before a
week is what that week's rankings use. Delete those rows to go back to the defaults.
"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse  # noqa: E402
import json
import logging
from collections import defaultdict
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

DEFAULT_WEIGHTS = {"vegas_props": 0.30, "experts": 0.35, "espn": 0.15, "model": 0.20}
# Displayed sources that blend as one group (each converted to points first, then averaged).
SOURCE_GROUPS = {"josh_hayden": "experts", "yahoo_consensus": "experts"}
PSEUDO_ROWS = 1500         # how many player-weeks of evidence the prior counts as
MIN_POOLED_ROWS = 300      # fewer rows than this: keep the prior
PROPS_FLOOR = 0.15
MIN_WEIGHT = 0.05
RELEVANT_RANKS = {"QB": 24, "RB": 36, "WR": 36, "TE": 36}
POSITIONS = ("QB", "RB", "WR", "TE")
SCORING_PREFERENCE = ("ppr", "half", "std")    # one row per player-week, in this order


@dataclass(frozen=True)
class Sample:
    sources: Mapping[str, float]          # by group
    actual: float
    position: str = ""
    week: int = 0


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


def group_of(source: str) -> str:
    return SOURCE_GROUPS.get(source, source)


def grouped(points: Mapping[str, float]) -> dict[str, float]:
    """Per-source points -> per-group points (the experts averaged)."""
    groups: dict[str, list[float]] = defaultdict(list)
    for source, value in points.items():
        group = group_of(source)
        if group in DEFAULT_WEIGHTS:
            groups[group].append(value)
    return {group: sum(values) / len(values) for group, values in groups.items()}


def source_shares(sources: Sequence[str], weights: Mapping[str, float]) -> dict[str, float]:
    """Each displayed source's share of a player's blend (a group's weight split among its sources)."""
    groups: dict[str, list[str]] = defaultdict(list)
    for source in sources:
        if group_of(source) in weights:
            groups[group_of(source)].append(source)
    total = sum(weights[g] for g in groups) or 1.0
    return {s: weights[g] / total / len(members) for g, members in groups.items() for s in members}


def blend(weights: Mapping[str, float], sources: Mapping[str, float]) -> float | None:
    """Weighted mean over the groups present (sources may be per-source or per-group)."""
    values = grouped(sources)
    total = sum(weights.get(k, 0.0) for k in values)
    return sum(weights.get(k, 0.0) * v for k, v in values.items()) / total if total else None


def mae(weights: Mapping[str, float], samples: Sequence[Sample]) -> float:
    errors = [abs(b - s.actual) for s in samples if (b := blend(weights, s.sources)) is not None]
    return round(mean(errors), 3) if errors else 0.0


def constrain(weights: Mapping[str, float]) -> dict[str, float]:
    """Non-negative, props >= PROPS_FLOOR, others >= MIN_WEIGHT, summing to 1."""
    floors = {k: PROPS_FLOOR if k == "vegas_props" else MIN_WEIGHT for k in weights}
    fixed: dict[str, float] = {}
    while True:
        free = {k: max(v, 0.0) for k, v in weights.items() if k not in fixed}
        room = 1.0 - sum(fixed.values())
        total = sum(free.values()) or 1.0
        scaled = {k: v / total * room for k, v in free.items()}
        low = {k for k, v in scaled.items() if v < floors[k]}
        if not low:
            return {**{k: round(v, 4) for k, v in fixed.items()}, **{k: round(v, 4) for k, v in scaled.items()}}
        fixed.update({k: floors[k] for k in low})


def fit_pooled(samples: Sequence[Sample], prior: Mapping[str, float] = DEFAULT_WEIGHTS,
               pseudo_rows: float = PSEUDO_ROWS) -> dict[str, float]:
    """Ridge least squares toward the prior on all rows (missing groups filled with the row mean)."""
    keys = list(prior)
    if len(samples) < MIN_POOLED_ROWS:
        return dict(prior)
    x = np.zeros((len(samples), len(keys)))
    for row, sample in enumerate(samples):
        present = [sample.sources[k] for k in keys if k in sample.sources]
        fill = sum(present) / len(present) if present else 0.0
        x[row] = [sample.sources.get(k, fill) for k in keys]
    y = np.array([s.actual for s in samples])
    p = np.array([prior[k] for k in keys])
    strength = pseudo_rows * float(np.mean(np.sum(x * x, axis=1)) / len(keys))
    w = np.linalg.solve(x.T @ x + strength * np.eye(len(keys)), x.T @ y + strength * p)
    w = w / w.sum() if w.sum() > 0 else p
    return constrain(dict(zip(keys, w.tolist())))


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
    if not set(DEFAULT_WEIGHTS) <= set(learned):
        return dict(DEFAULT_WEIGHTS), None          # learned under the old five-source blend
    return {k: learned[k] for k in DEFAULT_WEIGHTS}, latest


def position_weights(session: Session, season: int | None = None,
                     before_week: int | None = None) -> dict[str, dict[str, float]]:
    """Per-position weights: none any more (one pooled fit is steadier); kept for callers."""
    return {}


def samples_for_week(session: Session, season: int, week: int, relevant_only: bool = True) -> list[Sample]:
    """Pre-kickoff group points and actual points for every player who played that week."""
    rows = session.scalars(select(ProjectionHistory).where(
        ProjectionHistory.season == season, ProjectionHistory.week == week)).all()
    chosen: dict[int, ProjectionHistory] = {}
    for row in sorted(rows, key=lambda r: SCORING_PREFERENCE.index(r.scoring) if r.scoring in SCORING_PREFERENCE else 9):
        chosen.setdefault(row.player_id, row)
    if relevant_only:
        by_position: dict[str, list[ProjectionHistory]] = defaultdict(list)
        for row in chosen.values():
            by_position[row.position].append(row)
        keep = {r.player_id for pos, group in by_position.items()
                for r in sorted(group, key=lambda r: -r.if_active)[:RELEVANT_RANKS.get(pos, 36)]}
        chosen = {pid: row for pid, row in chosen.items() if pid in keep}
    actuals = {scoring: actual_points(session, season, scoring) for scoring in {r.scoring for r in chosen.values()}}
    samples = []
    for row in chosen.values():
        actual = actuals[row.scoring].get((row.player_id, week))
        sources = grouped(json.loads(row.sources))
        if actual is not None and sources:
            samples.append(Sample(sources, actual, row.position, week))
    return samples


def learn(session: Session, season: int, apply: bool = True, through_week: int | None = None) -> list[Update]:
    """Refit the weights after every completed, not-yet-learned week (up to through_week)."""
    _ensure_table(session)
    learned = set(session.scalars(select(LearnedWeights.week).where(LearnedWeights.season == season)))
    weeks = sorted(set(session.scalars(select(ProjectionHistory.week).where(ProjectionHistory.season == season))))
    pooled: list[Sample] = []
    current, _ = effective_weights(session, season, min(weeks) if weeks else None)
    schedule = load_schedule(session, season)
    complete = {w for w in weeks if any(g.week == w for g in schedule.games)
                and all(g.is_final for g in schedule.games if g.week == w)}
    updates = []
    for week in weeks:
        if (through_week is not None and week > through_week) or week not in complete:
            continue
        samples = samples_for_week(session, season, week)
        pooled += samples
        if week in learned:
            current, _ = effective_weights(session, season, week + 1)
            continue
        after = fit_pooled(pooled)
        update = Update(season, week, len(pooled), dict(current), after, after,
                        mae(current, samples), mae(after, samples))
        updates.append(update)
        if apply:
            session.add(LearnedWeights(season=season, week=week, rows=len(pooled), weights=json.dumps(after),
                                       optimal=json.dumps(after), mae_before=update.mae_before,
                                       mae_after=update.mae_after))
            session.commit()
        current = after
    return updates


def describe(update: Update) -> str:
    lines = [f"Week {update.week}: {update.rows} pooled player-weeks, that week's MAE {update.mae_before} "
             f"-> {update.mae_after}"]
    for key in DEFAULT_WEIGHTS:
        lines.append(f"  {key:12} {update.before[key]:.3f} -> {update.after[key]:.3f}")
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
