"""Backfill past weeks: outside sources, pre-kickoff projections, and the learned weights.

Run from backend/:
    python -m services.history_backfill --season 2026 --weeks 1-6
    python -m services.history_backfill --season 2026 --weeks 1-6 --no-fetch   # rebuild from stored sources

Yahoo's weekly ranks (consensus plus the tracked experts, Josh & Hayden among them) and
ESPN's projections are still published for past weeks, for free. Vegas props are not
backfilled (historical odds cost Odds API credits).

The projections are rebuilt as they would have looked before kickoff, without hindsight:
- the model only sees games before that week (stats truncated, defense strength and EPA
  profiles rebuilt from earlier games, as in services/backtest.py);
- injuries come from that week's official report, with play rates learned from other seasons;
- the blend uses the weights learned from earlier weeks only.

Rows already in projection_history (saved live before kickoff) are kept. Then the
season's learned weights are re-learned week by week in order, so each update only uses
weeks that came before it.
"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse  # noqa: E402
import json
import logging
from types import SimpleNamespace

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from database.models import LearnedWeights, ProjectionHistory
from integrations import espn, yahoo_experts
from integrations.weekly_sources import SourceError
from services import weight_learning
from services.backtest import truncate
from services.injuries import build_context
from services.league import LeagueSettings
from services.rankings_store import _ensure_tables
from services.schedule_strength import defense_strength
from services.valuation import build_valuation
from services.weekly_rankings import (
    _Candidate, _load_sources, blend, model_factors, player_rates,
)
from services.weight_learning import effective_weights, position_weights

log = logging.getLogger(__name__)
SCORINGS = ("ppr", "half", "std")


def fetch_sources(session: Session, http: httpx.Client, season: int, week: int) -> dict[str, str]:
    """Re-pull Yahoo weekly ranks and ESPN projections for a past week. {source: result}."""
    results = {}
    for name, refresh in (("yahoo_experts", lambda: yahoo_experts.refresh(session, http, season, week,
                                                                          include_ros=False)),
                          ("espn", lambda: espn.refresh(session, http, season, week))):
        try:
            results[name] = f"{refresh()} rows"
        except SourceError as exc:
            session.rollback()
            results[name] = f"failed: {exc}"
    return results


def point_in_time(valuation, week: int) -> SimpleNamespace:
    """The parts of a valuation the blend uses, from games before `week` only."""
    seasons = tuple(truncate(p, week, valuation.schedule) for p in valuation.seasons)
    return SimpleNamespace(season=valuation.season, seasons=seasons, priors=valuation.priors,
                           schedule=valuation.schedule, defense=defense_strength(list(seasons), valuation.schedule))


def project_week(session: Session, league: LeagueSettings, week: int):
    """The week's blended projections as of kickoff (see the module docstring)."""
    full = build_valuation(session, league)
    valuation = point_in_time(full, week)
    candidates = _load_sources(session, valuation.season, week, league.scoring)
    has_outside_sources = any(c.espn is not None or c.expert_ranks for c in candidates.values())
    for p in valuation.seasons:
        if p.position and p.games and p.player_id not in candidates:
            candidates[p.player_id] = _Candidate(p.player_id, p.name, p.position, p.team)
    rates = player_rates(valuation)
    context = build_context(session, valuation.seasons, rates, valuation.season, week, valuation.schedule,
                            use_live=False, exclude_rate_season=valuation.season)
    weights, _ = effective_weights(session, valuation.season, week)
    return blend(candidates, valuation, week, league, require_outside_source=has_outside_sources,
                 context=context, rates=rates, weights=weights,
                 factor_for=model_factors(session, valuation.season, week),
                 weights_by_position=position_weights(session, valuation.season, week))


def record_week(session: Session, season: int, week: int, scoring: str, players) -> int:
    """Insert projection_history rows the live app didn't save; existing rows are kept."""
    _ensure_tables(session)
    existing = set(session.scalars(select(ProjectionHistory.player_id).where(
        ProjectionHistory.season == season, ProjectionHistory.week == week, ProjectionHistory.scoring == scoring)))
    written = 0
    for player in players:
        if player.player_id in existing:
            continue
        session.add(ProjectionHistory(
            season=season, week=week, scoring=scoring, player_id=player.player_id, position=player.position,
            projection=player.projection, if_active=player.if_active, play_prob=player.play_prob,
            floor=player.floor, ceiling=player.ceiling,
            sources=json.dumps({name: round(v.points, 2) for name, v in player.sources.items()})))
        written += 1
    session.commit()
    return written


def backfill(session: Session, season: int, weeks: list[int], http: httpx.Client | None = None) -> dict:
    """Fetch (when http is given) and rebuild each week, re-learning the season's weights in week order."""
    session.execute(delete(LearnedWeights).where(LearnedWeights.season == season))
    session.commit()
    report: dict = {"weeks": {}, "updates": []}
    for week in sorted(weeks):
        entry = {"sources": fetch_sources(session, http, season, week) if http else {}}
        entry["history"] = {scoring: record_week(session, season, week, scoring,
                                                 project_week(session, LeagueSettings(scoring=scoring), week))
                            for scoring in SCORINGS}
        report["updates"] += weight_learning.learn(session, season, through_week=week)
        report["weeks"][week] = entry
    report["updates"] += weight_learning.learn(session, season)       # later weeks saved live
    return report


def _weeks(text: str) -> list[int]:
    first, _, last = text.partition("-")
    return list(range(int(first), int(last or first) + 1))


def main(argv: list[str] | None = None) -> None:
    from database.db import engine

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--weeks", required=True, help="a week or a range, e.g. 1-6")
    parser.add_argument("--no-fetch", action="store_true", help="use the sources already stored")
    args = parser.parse_args(argv)
    with Session(engine) as session:
        if args.no_fetch:
            report = backfill(session, args.season, _weeks(args.weeks))
        else:
            with httpx.Client(timeout=30, follow_redirects=True) as http:
                report = backfill(session, args.season, _weeks(args.weeks), http)
    for week, entry in report["weeks"].items():
        log.info("Week %s: sources %s, history %s", week, entry["sources"], entry["history"])
    for update in report["updates"]:
        log.info(weight_learning.describe(update))


if __name__ == "__main__":
    main()
