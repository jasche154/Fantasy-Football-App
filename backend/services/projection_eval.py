"""Score saved weekly projections against what actually happened.

Run from backend/:
    python -m services.projection_eval --season 2026 --scoring ppr

Reads projection_history (written every time Week X rankings are built before kickoff) and
the box scores loaded by populate_stats. For player-weeks the player played, it reports the
mean absolute error of the blended if-active projection and of each source alone, on the
games that source covered (with the blend's error on the same games, for a fair comparison).

`suggested_weights` is inverse-MSE weighting across sources: a starting point for learning
the blend from results as weeks accumulate. It is reported, never applied automatically.
"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse  # noqa: E402
import json
import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping
from statistics import mean

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import PlayerStatsWeekly, ProjectionHistory
from services import rankings_store
from services.scoring import POINTS_PER_RECEPTION

log = logging.getLogger(__name__)
MIN_ROWS = 30          # per source, before a weight is suggested


def actual_points(session: Session, season: int, scoring: str) -> dict[tuple[int, int], float]:
    """{(player_id, week): fantasy points} in the given scoring, regular season."""
    per_catch = POINTS_PER_RECEPTION[scoring]
    rows = session.execute(select(
        PlayerStatsWeekly.player_id, PlayerStatsWeekly.week, PlayerStatsWeekly.fantasy_points,
        PlayerStatsWeekly.receptions).where(
        PlayerStatsWeekly.season == season, PlayerStatsWeekly.season_type == "REG"))
    return {(pid, week): (points or 0.0) + per_catch * (catches or 0) for pid, week, points, catches in rows}


def evaluate(rows: Iterable[ProjectionHistory], actuals: Mapping[tuple[int, int], float]) -> dict:
    blend_errors: list[float] = []
    by_source: dict[str, list[tuple[float, float]]] = defaultdict(list)   # (source error, blend error)
    by_position: dict[str, list[float]] = defaultdict(list)
    weeks: set[int] = set()
    for row in rows:
        actual = actuals.get((row.player_id, row.week))
        if actual is None:
            continue                                   # didn't play (or stats not loaded yet)
        weeks.add(row.week)
        blend_error = abs(row.if_active - actual)
        blend_errors.append(blend_error)
        by_position[row.position].append(blend_error)
        for source, points in json.loads(row.sources).items():
            by_source[source].append((abs(points - actual), blend_error))
    sources = {name: {"n": len(pairs), "mae": round(mean(e for e, _ in pairs), 2),
                      "blend_mae_same_games": round(mean(b for _, b in pairs), 2)}
               for name, pairs in sorted(by_source.items())}
    inverse = {name: 1 / mean(e * e for e, _ in pairs) for name, pairs in by_source.items()
               if len(pairs) >= MIN_ROWS and any(e for e, _ in pairs)}
    total = sum(inverse.values())
    return {
        "weeks": sorted(weeks), "n": len(blend_errors),
        "blend_mae": round(mean(blend_errors), 2) if blend_errors else None,
        "by_position": {p: round(mean(e), 2) for p, e in sorted(by_position.items())},
        "sources": sources,
        "suggested_weights": {name: round(v / total, 3) for name, v in sorted(inverse.items())} if total else {},
    }


def main(argv: list[str] | None = None) -> None:
    from database.db import engine

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--scoring", choices=sorted(POINTS_PER_RECEPTION), default="ppr")
    args = parser.parse_args(argv)
    with Session(engine) as session:
        report = evaluate(rankings_store.history(session, args.season, args.scoring),
                          actual_points(session, args.season, args.scoring))
    if not report["n"]:
        log.info("No scored projections yet: build Week X rankings before kickoff, then load that week's stats.")
        return
    log.info(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
