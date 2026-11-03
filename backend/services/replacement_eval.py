"""Out-of-sample check of the replacement level behind player values (VORP).

Run from backend/ (read-only; point DATABASE_URL at a copy if you like):
    python -m services.replacement_eval --seasons 2021-2025 --weeks 4,8

Compares three ways to set each position's replacement player:
- ours: services.league.replacement_levels (starters, flex included, plus each position's share of
  the bench spots, split by starters);
- gwttkb: a fixed per-team depth {QB 1.17, RB 2.83, WR 3.67, TE 0.75} x teams (the published
  GWTTKB baseline; its half-PPR/standard tweak for RB/WR isn't published as numbers, so the same
  depths are used in every format);
- starters: the first non-starter (flex included), a neutral lineup-only baseline.

For each season and cut-off week W, the model projects each player's rest-of-season rate from
weeks 1..W plus last season only (services.valuation.value_players, no consensus, no schedule:
point in time). Predicted VORP = max(0, projected rate - the scheme's replacement level among
those projections). The target is what happened after W: actual points over weeks W+1..end
minus the scheme's replacement rate (from the actual rates) x games played, floored at 0, so a
missed game is worth replacement. Every target depends on a depth choice, so each prediction
is scored against every scheme's target (Spearman rank correlation over the players any
scheme values above 0), and a scheme only "wins" if it beats ours in every target column.
"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
from collections.abc import Iterable, Mapping, Sequence  # noqa: E402
from dataclasses import replace  # noqa: E402

import numpy as np  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from services.league import POSITIONS, LeagueSettings, replacement_levels, starter_counts  # noqa: E402
from services.projections import PlayerSeason, load_player_seasons  # noqa: E402
from services.valuation import value_players  # noqa: E402

GWTTKB_DEPTH = {"QB": 1.17, "RB": 2.83, "WR": 3.67, "TE": 0.75}
SCHEMES = ("ours", "gwttkb", "starters")
MIN_ROS_GAMES = 3          # players need this many games after the cut-off to set the actual replacement rate
log = logging.getLogger(__name__)


def _pools(pairs: Iterable[tuple[str | None, float]]) -> dict[str, list[float]]:
    pools: dict[str, list[float]] = {p: [] for p in POSITIONS}
    for position, ppg in pairs:
        if position in pools:
            pools[position].append(ppg)
    return {p: sorted(v, reverse=True) for p, v in pools.items()}


def scheme_levels(scheme: str, pairs: Sequence[tuple[str | None, float]], league: LeagueSettings) -> dict[str, float]:
    """Replacement points per game at each position under `scheme`."""
    if scheme == "ours":
        return replacement_levels(pairs, league)
    pools = _pools(pairs)
    if scheme == "gwttkb":
        depth = {p: round(GWTTKB_DEPTH[p] * league.teams) for p in POSITIONS}
    elif scheme == "starters":
        depth = starter_counts(pools, league)
    else:
        raise ValueError(f"unknown scheme {scheme!r}")
    return {p: pools[p][depth[p]] if depth[p] < len(pools[p]) else 0.0 for p in POSITIONS}


def spearman(a: Sequence[float], b: Sequence[float]) -> float:
    """Rank correlation with tied values sharing their average rank."""
    def ranks(values: Sequence[float]) -> np.ndarray:
        arr = np.asarray(values, dtype=float)
        order = np.empty(len(arr))
        order[arr.argsort(kind="mergesort")] = np.arange(len(arr))
        _, inverse, counts = np.unique(arr, return_inverse=True, return_counts=True)
        return (np.bincount(inverse, weights=order) / counts)[inverse]
    if len(a) < 3:
        return float("nan")
    return float(np.corrcoef(ranks(a), ranks(b))[0, 1])


def _truncate(seasons: Sequence[PlayerSeason], week: int) -> list[PlayerSeason]:
    cut = [replace(p, games=tuple(g for g in p.games if g.week <= week)) for p in seasons]
    return [p for p in cut if p.games]


def evaluate_cut(full: Sequence[PlayerSeason], previous: Sequence[PlayerSeason], league: LeagueSettings,
                 week: int, end_week: int) -> dict[str, dict[str, float]]:
    """{prediction scheme: {target scheme: Spearman}} for one season cut off after `week`."""
    current = _truncate(full, week)
    values, _ = value_players(current, previous, league, end_week - week)
    projected = {v.player_id: (v.position, v.ros_ppg) for v in values}
    ros = {}
    for p in full:
        games = [g for g in p.games if week < g.week <= end_week]
        if games:
            ros[p.player_id] = (p.position, sum(g.fantasy_points for g in games), len(games))
    actual_rates = [(pos, total / n) for pos, total, n in ros.values() if n >= MIN_ROS_GAMES]
    predicted, actual = {}, {}
    for scheme in SCHEMES:
        levels = scheme_levels(scheme, list(projected.values()), league)
        predicted[scheme] = {pid: max(0.0, ppg - levels.get(pos, 0.0)) for pid, (pos, ppg) in projected.items()}
        realized = scheme_levels(scheme, actual_rates, league)
        actual[scheme] = {pid: max(0.0, ros[pid][1] - realized.get(ros[pid][0], 0.0) * ros[pid][2])
                          if pid in ros else 0.0 for pid in projected}
    players = sorted(pid for pid in projected
                     if any(table[pid] > 0 for table in (*predicted.values(), *actual.values())))
    corr = {pred: {target: round(spearman([predicted[pred][p] for p in players],
                                          [actual[target][p] for p in players]), 4)
                   for target in SCHEMES} for pred in SCHEMES}
    # Ordering of the players who turned out to matter (actual value above 0 for that target).
    mattered = {target: [p for p in players if actual[target][p] > 0] for target in SCHEMES}
    relevant = {pred: {target: round(spearman([predicted[pred][p] for p in mattered[target]],
                                              [actual[target][p] for p in mattered[target]]), 4)
                       for target in SCHEMES} for pred in SCHEMES}
    return {"all": corr, "mattered": relevant}


def summarize(results: Sequence[Mapping[str, Mapping[str, float]]]) -> dict[str, dict[str, float]]:
    """Mean correlation per (prediction, target) over seasons and cut-offs."""
    return {pred: {target: round(float(np.nanmean([r[pred][target] for r in results])), 4) for target in SCHEMES}
            for pred in SCHEMES}


def gwttkb_wins(summary: Mapping[str, Mapping[str, float]]) -> bool:
    """True only if GWTTKB's depth predicts better than ours against every target."""
    return all(summary["gwttkb"][t] > summary["ours"][t] for t in SCHEMES)


def season_wins(rows: Sequence[Mapping]) -> dict[str, int]:
    """Per metric: in how many season cut-offs GWTTKB beats ours against every target."""
    return {metric: sum(gwttkb_wins(r["corr"][metric]) for r in rows) for metric in ("all", "mattered")}


def run(session: Session, seasons: Iterable[int], weeks: Sequence[int], league: LeagueSettings,
        end_week: int) -> dict:
    scoring = league.scoring_settings()
    rows = []
    for season in seasons:
        full = load_player_seasons(session, season, scoring)
        previous = load_player_seasons(session, season - 1, scoring)
        if not full:
            log.warning("%s: no stats loaded, skipped", season)
            continue
        for week in weeks:
            rows.append({"season": season, "week": week, "corr": evaluate_cut(full, previous, league, week, end_week)})
    summary = {metric: summarize([r["corr"][metric] for r in rows]) for metric in ("all", "mattered")} if rows else {}
    return {"league": {"scoring": league.scoring, "teams": league.teams, "superflex": league.superflex},
            "rows": rows, "summary": summary, "cuts": len(rows), "season_wins": season_wins(rows),
            "gwttkb_wins": bool(rows) and all(gwttkb_wins(s) for s in summary.values())}


def _seasons(text: str) -> list[int]:
    first, _, last = text.partition("-")
    return list(range(int(first), int(last or first) + 1))


def main(argv: list[str] | None = None) -> None:
    from config import get_settings
    from database.db import engine

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seasons", default="2021-2025")
    parser.add_argument("--weeks", default="4,8")
    parser.add_argument("--scoring", default="ppr,half,std")
    parser.add_argument("--teams", default="10,12")
    args = parser.parse_args(argv)
    weeks = [int(w) for w in args.weeks.split(",")]
    out = []
    with Session(engine) as session:
        for scoring in args.scoring.split(","):
            for teams in (int(t) for t in args.teams.split(",")):
                result = run(session, _seasons(args.seasons), weeks, LeagueSettings(scoring=scoring, teams=teams),
                             get_settings().fantasy_end_week)
                out.append({k: result[k] for k in ("league", "summary", "cuts", "season_wins", "gwttkb_wins")})
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
