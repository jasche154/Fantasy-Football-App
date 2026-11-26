"""Simulated seasons: fill-in quarterbacks, measured the same way on real and simulated seasons.

A FILL-IN start is a start (the passing-yards leader, sim_qb.starters) by a QB other than his team's job holder
while the holder carries an injury tag that week (hurt, suspended); the holder is the team's last starter who was
not a fill-in, so a healthy holder who does not play has lost the job and his replacement is the new holder (one
who left a game early, untagged, keeps it).
A RUN is a fill-in's consecutive fill-in starts for one team. For every run of MIN_RUN or more starts (its first
in week from_week or later): his points a game over the run, whether the holder started again that season, and
after his 2nd, 3rd and 4th start (the team's next game, before it is played) his rank among the QBs by that week's
projection and his expert rank (SeasonData.expert_ranks: a real season's FantasyPros rest-of-season ranks, 2020
on; a simulated week's own).

    python -m services.sim_qb_fillins --real 2019-2025
    python -m services.sim_qb_fillins --sim 2027 [--seeds 200] [--workers 3] [--set market.worth_step=0.4]
"""
import argparse
import json
import logging
import os
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from functools import partial

import numpy as np

from services import sim_qb
from services.replay_pool import SeasonData

MIN_RUN = 3                      # a run of this many starts is a fill-in stint, not a one-off
AFTER = (2, 3, 4)                # ranks read after this many starts
TOP = (12, 15)                   # shares of runs ranked in the top this many after the last of AFTER starts
HOT = 20.0                       # points a game of a top-6 QB (half PPR): a fill-in scoring like a star
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Run:
    team: str
    player: int
    weeks: tuple[int, ...]       # his fill-in starts
    ppg: float
    holder_back: bool            # the holder started again later that season
    projection_rank: dict        # {k: rank among QBs by projection before the team's game after start k}
    expert_rank: dict            # {k: expert rank then}
    lift: dict = None            # {k: (that projection - his first start's, his mean points in k starts - it)}


def _starts_by_team(data: SeasonData) -> dict[str, dict[int, int]]:
    by_team: dict[str, dict[int, int]] = defaultdict(dict)
    for (team, week), pid in sim_qb.starters(data).items():
        by_team[team][week] = pid
    return by_team


def _projection_ranks(data: SeasonData, week: int) -> dict[int, int]:
    qbs = sorted(((points, pid) for (pid, w), points in data.projected.items()
                  if w == week and pid in data.players and data.players[pid].position == "QB"), reverse=True)
    return {pid: rank for rank, (_, pid) in enumerate(qbs, start=1)}


def runs(data: SeasonData, from_week: int = 1) -> list[Run]:
    """Every fill-in run of MIN_RUN or more starts beginning in week from_week or later (module docstring)."""
    found = []
    for team, weeks in sorted(_starts_by_team(data).items()):
        order = sorted(weeks)
        holder, stint = weeks[order[0]], []
        for week in order[1:]:
            starter = weeks[week]
            filling = starter != holder and (holder, week) in data.injuries
            if filling and stint and weeks[stint[-1]] == starter:
                stint.append(week)
                continue
            if len(stint) >= MIN_RUN and stint[0] >= from_week:
                found.append(_run(data, team, holder, stint, weeks))
            stint = [week] if filling else []
            if not filling and starter != holder and (holder, week) not in data.box:
                holder = starter                      # a healthy holder who did not play: the job moved
        if len(stint) >= MIN_RUN and stint[0] >= from_week:
            found.append(_run(data, team, holder, stint, weeks))
    return found


def _run(data: SeasonData, team: str, holder: int, stint: list[int], weeks: dict[int, int]) -> Run:
    pid, order = weeks[stint[0]], sorted(weeks)
    points = [data.actual.get((pid, w), 0.0) for w in stint]
    proj, expert, lift = {}, {}, {}
    first = data.projected.get((pid, stint[0]))
    for k in AFTER:
        if k > len(stint):
            continue
        later = [w for w in order if w > stint[k - 1]]
        week = later[0] if later else stint[k - 1] + 1
        proj[k] = _projection_ranks(data, week).get(pid)
        expert[k] = data.expert_ranks.get((pid, week))
        if first and data.projected.get((pid, week)):
            lift[k] = (data.projected[(pid, week)] - first, float(np.mean(points[:k])) - first)
    back = any(w > stint[-1] and weeks[w] == holder for w in order)
    return Run(team, pid, tuple(stint), float(np.mean(points)), back, proj, expert, lift)


def summary(found: list[Run], seasons: int) -> dict[str, float]:
    """Means over the runs: runs a season, points a game, median projection and expert rank after each of AFTER
    starts, and the share ranked in the expert top 12 / 15 after the last (the top 12 also while the holder came
    back that season)."""
    ppg = [r.ppg for r in found]
    out = {"runs per season": len(found) / max(1, seasons), "fill-in ppg": float(np.mean(ppg)) if found else float("nan"),
           "fill-in ppg sd": float(np.std(ppg)) if found else float("nan"),
           f"fill-in {HOT}+ ppg %": 100.0 * float(np.mean([p >= HOT for p in ppg])) if found else float("nan")}
    for k in AFTER:
        proj = [r.projection_rank[k] for r in found if r.projection_rank.get(k)]
        expert = [r.expert_rank[k] for r in found if r.expert_rank.get(k)]
        out[f"projection rank after {k} (median)"] = float(np.median(proj)) if proj else float("nan")
        out[f"projection rank after {k} (best tenth)"] = float(np.percentile(proj, 10)) if proj else float("nan")
        out[f"projection top-{TOP[0]} after {k} %"] = 100.0 * float(np.mean([p <= TOP[0] for p in proj])) if proj else float("nan")
        out[f"expert rank after {k} (median)"] = float(np.median(expert)) if expert else float("nan")
        out[f"expert rank after {k} (best quarter)"] = float(np.percentile(expert, 25)) if expert else float("nan")
        pairs = np.array([r.lift[k] for r in found if r.lift and k in r.lift])
        out[f"projection lift per point after {k}"] = (float((pairs[:, 0] * pairs[:, 1]).sum() / (pairs[:, 1] ** 2).sum())
                                                       if len(pairs) > 2 else float("nan"))
    last = AFTER[-1]
    ranked = [r for r in found if r.expert_rank.get(last)]
    for top in TOP:
        out[f"expert top-{top} after {last} %"] = (100.0 * sum(r.expert_rank[last] <= top for r in ranked) / len(ranked)
                                                    if ranked else float("nan"))
    back = [r for r in found if r.holder_back]
    out["expert top-12 while holder back (any start) %"] = (
        100.0 * sum(any(rank and rank <= 12 for rank in r.expert_rank.values()) for r in back) / len(back)
        if back else float("nan"))
    out["runs with expert ranks"] = float(len(ranked))
    return out


def real(seasons: list[int]) -> dict[str, float]:
    from database.db import SessionLocal
    from services import replay_pool
    from services.sim_eval import SCORING

    found = []
    with SessionLocal() as session:
        for season in seasons:
            found += runs(replay_pool.load_season_data(session, season, SCORING))
    return summary(found, len(seasons))


def _sim_seed(seed: int, season: int, overrides: tuple[str, ...] = ()) -> list[Run]:
    from database.db import SessionLocal
    from services import sim_constants, sim_qb_fit

    for item in overrides:                       # sim_eval --set's form: section.key=JSON value
        name, _, value = item.partition("=")
        section, _, key = name.partition(".")
        sim_constants.override(section, key, json.loads(value))
    with SessionLocal() as session:
        data, first, *_ = sim_qb_fit._league(session, season, seed)
    return runs(data, first)


def simulated(season: int, seeds: int, workers: int, overrides: tuple[str, ...] = ()) -> dict[str, float]:
    run = partial(_sim_seed, season=season, overrides=overrides)
    if workers <= 1:
        found = [r for seed in range(1, seeds + 1) for r in run(seed)]
    else:
        with ProcessPoolExecutor(workers) as pool:
            found = [r for rows in pool.map(run, range(1, seeds + 1)) for r in rows]
    return summary(found, seeds)


def main(argv: list[str] | None = None) -> None:
    from services.replay_data import parse_seasons     # the CLI's only use of the importer

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--real", help="seasons, e.g. 2019-2025")
    parser.add_argument("--sim", type=int, help="a simulated season, e.g. 2027 or 2026 (from its live week)")
    parser.add_argument("--seeds", type=int, default=200)
    parser.add_argument("--workers", type=int, default=min(3, os.cpu_count() or 1))
    parser.add_argument("--set", action="append", default=[], help="override a sim_params.json value, e.g. market.worth_step=0.4")
    args = parser.parse_args(argv)
    if not args.real and not args.sim:
        parser.print_help()
        return
    report = real(parse_seasons(args.real)) if args.real else simulated(args.sim, args.seeds, args.workers, tuple(args.set))
    for key, value in report.items():
        log.info("%-48s %8.2f", key, value)


if __name__ == "__main__":
    main()
