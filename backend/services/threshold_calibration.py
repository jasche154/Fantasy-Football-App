"""Are the "N+ points" odds honest? Predicted chance vs how often it happened, on saved weeks.

Run from backend/ (reads only):
    python -m services.threshold_calibration --season 2026 --scoring ppr

Point in time: each row is a saved pre-kickoff projection (projection_history: if-active
points and chance to play), the shape inputs only use games before that week, and the
score ranges (services/score_ranges.json) were fit on earlier seasons. A player who didn't
play counts as 0 points (the odds already include the chance he sits).

Reported per threshold rank (low / mid / high, e.g. WR 10/15/20) and overall: predicted
probabilities in bins of 0.1 with the actual hit rate, the mean predicted vs actual rate, and
the Brier score (with the Brier score of always predicting the overall hit rate, for scale).
"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse  # noqa: E402
import json
import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import PlayerStatsWeekly
from services import rankings_store, score_ranges
from services.projection_eval import actual_points
from services.schedule_strength import load_schedule
from services.scoring import POINTS_PER_RECEPTION
from services.threshold_odds import THRESHOLDS, threshold_odds

log = logging.getLogger(__name__)
BIN_WIDTH = 0.1
LABELS = ("low", "mid", "high")


def completed_weeks(session: Session, season: int) -> set[int]:
    """Weeks whose games are all final and whose box scores are loaded (a half-loaded week reads as DNPs)."""
    loaded = set(session.scalars(select(PlayerStatsWeekly.week).distinct().where(
        PlayerStatsWeekly.season == season, PlayerStatsWeekly.season_type == "REG")))
    games = load_schedule(session, season).games
    return {w for w in loaded if any(g.week == w for g in games) and all(g.is_final for g in games if g.week == w)}


def predictions(session: Session, season: int, scoring: str) -> list[tuple[str, int, float, bool]]:
    """(position, threshold index, predicted probability, hit) per saved player-week and threshold."""
    complete = completed_weeks(session, season)
    rows = [r for r in rankings_store.history(session, season, scoring)
            if r.week in complete and r.position in THRESHOLDS]
    actuals = actual_points(session, season, scoring)
    by_week: dict[int, list] = defaultdict(list)
    for row in rows:
        by_week[row.week].append(row)
    out = []
    for week, week_rows in sorted(by_week.items()):
        shapes = score_ranges.shape_inputs(session, season, [r.player_id for r in week_rows], before_week=week)
        for row in week_rows:
            quantiles = score_ranges.player_quantiles(row.position, row.if_active,
                                                      *shapes.get(row.player_id, (None, None)))
            actual = actuals.get((row.player_id, row.week), 0.0)
            for index, (threshold, probability) in enumerate(threshold_odds(row.position, quantiles, row.play_prob)):
                out.append((row.position, index, probability, actual >= threshold))
    return out


def _bins(pairs: Sequence[tuple[float, bool]]) -> list[dict]:
    grouped: dict[int, list[tuple[float, bool]]] = defaultdict(list)
    for probability, hit in pairs:
        grouped[min(int(probability / BIN_WIDTH), int(1 / BIN_WIDTH) - 1)].append((probability, hit))
    return [{"bin": f"{b * BIN_WIDTH:.1f}-{(b + 1) * BIN_WIDTH:.1f}", "n": len(items),
             "predicted": round(sum(p for p, _ in items) / len(items), 3),
             "actual": round(sum(h for _, h in items) / len(items), 3)}
            for b, items in sorted(grouped.items())]


def summarize(pairs: Sequence[tuple[float, bool]]) -> dict:
    if not pairs:
        return {"n": 0}
    rate = sum(h for _, h in pairs) / len(pairs)
    return {"n": len(pairs), "mean_predicted": round(sum(p for p, _ in pairs) / len(pairs), 3),
            "hit_rate": round(rate, 3),
            "brier": round(sum((p - h) ** 2 for p, h in pairs) / len(pairs), 4),
            "brier_base_rate": round(sum((rate - h) ** 2 for _, h in pairs) / len(pairs), 4),
            "bins": _bins(pairs)}


def report(rows: Iterable[tuple[str, int, float, bool]]) -> dict:
    rows = list(rows)
    by_level: Mapping[str, list] = {label: [(p, h) for _, i, p, h in rows if i == index]
                                    for index, label in enumerate(LABELS)}
    by_position: dict[str, list] = defaultdict(list)
    for position, _, probability, hit in rows:
        by_position[position].append((probability, hit))
    return {"overall": summarize([(p, h) for _, _, p, h in rows]),
            "by_threshold": {label: summarize(pairs) for label, pairs in by_level.items()},
            "by_position": {pos: {k: v for k, v in summarize(pairs).items() if k != "bins"}
                            for pos, pairs in sorted(by_position.items())}}


def main(argv: list[str] | None = None) -> None:
    from database.db import engine

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--scoring", choices=sorted(POINTS_PER_RECEPTION), default="ppr")
    args = parser.parse_args(argv)
    with Session(engine) as session:
        result = report(predictions(session, args.season, args.scoring))
    if not result["overall"]["n"]:
        log.info("No saved projections with final stats yet: not evaluated.")
        return
    log.info(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
