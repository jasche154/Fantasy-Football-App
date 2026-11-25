"""Simulated seasons: fit sim_qb.json (who holds the QB job, services/sim_qb.py) on real seasons, and check it.

    python -m services.sim_qb_fit --fit [--seasons 2019-2025]
    python -m services.sim_qb_fit --check --season 2027 [--seeds 200] [--workers 3]

--fit: each real season (half PPR) is walked with sim_qb.walk on its passing-yards leaders (sim_qb.starters, the
starter the engine and the metrics use too), with its drafted rookie QBs (nflverse picks, landing on the drafting
team; sim_next_year_fit.draft_class) and its established QBs (sim_qb_jobs.established_of: its real ADP and
sim_setup.qb_inputs). Every logistic is a ridge one (RIDGE on each standardized covariate, the
intercept free), stored on the raw scale. On the "week" rows of holders who are not established:
- `hazard`: a healthy job loss in log market rank, form and record (no drafted rookie on the team);
- `rookie`: a drafted rookie taking the job in log pick, record and form (a drafted rookie on the team, pick
  sim_qb.ROOKIE_PICKS or better; the event: the holder lost the job and a drafted rookie started);
- `return`: on the "return" rows of holders who are not established, the fill-in keeping the job, in log market
  rank;
- `elevated`: the mean real weekly hazard of the `hazard` rows (sim_qb_jobs: a job-in-danger rumour only above it);
- `prior`: logistic of a QB starting his first projected game in his first projections (sim_world.implied_role:
  the starts weight a projection implies, no cliff);
- `validation`: leave-season-out log loss and AUC of each model against no covariate (the hazard also against
  market rank only, and the hazard plus each candidate it leaves out, with its coefficient: starts over the
  last three seasons on any team, scaled to three full seasons when fewer are in the database (2018 on); points a
  start over them; nflverse draft pick and years since that draft (undrafted: pick UNDRAFTED, years = his
  seasons in the database, an undercount for a veteran from before 2018); the preseason expert rank, 2020 on);
- `losers`: the real holders who lost the job healthy, the established ones (whom the simulator protects)
  first.
--check: builds `seeds` seasons the way a league would (sim_setup: the future season simulated whole, or the live
one from its first unplayed week) and logs sim_qb.job_metrics per season (mean over seeds, plus the totals of
top-12 and established job losses), the rookie QBs who started a game after week 1 but not week 1
(sim_metrics.rookies), the seconds simulate() took, and for a live season how often the first simulated week's
starter is not the holder of the real weeks while that holder is healthy and does not play.
"""
import argparse
import json
import logging
import math
import os
import time
from collections import defaultdict
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor
from functools import partial

import numpy as np
from sqlalchemy import select

from services import replay_pool, sim_qb

log = logging.getLogger(__name__)

SCORING = "half"
RIDGE = 1.0                      # ridge penalty on each standardized coefficient (31 real losses: small and stable)
NEWTON_STEPS = 50
UNDRAFTED = 300                  # draft pick of an undrafted QB (the candidate covariate)
HISTORY = 3                      # seasons of starts history in the candidate covariates
FULL_SEASON = 17
EARLY_PROJECTIONS = 3            # as sim_world.EARLY_PROJECTIONS
FORM_POINTS = 12.0               # points a start a QB with no history is shrunk toward (candidate covariate)
MODELS = {"hazard": ("rank", "form", "record"), "rookie": ("rookie_pick", "record", "form"), "return": ("rank",)}


def _logit(x: np.ndarray, y: np.ndarray, ridge: float = RIDGE) -> np.ndarray:
    """Ridge logistic regression, column 0 the unpenalized intercept, the others standardized before the penalty
    (so it weighs each covariate alike) and the coefficients returned on the raw scale. Newton's method."""
    mean, sd = x[:, 1:].mean(axis=0), x[:, 1:].std(axis=0)
    sd = np.where(sd > 0, sd, 1.0)
    z = np.column_stack([np.ones(len(x)), (x[:, 1:] - mean) / sd])
    beta = np.zeros(x.shape[1])
    beta[0] = math.log(max(y.mean(), 1e-6) / max(1 - y.mean(), 1e-6))
    penalty = ridge * np.eye(x.shape[1])
    penalty[0, 0] = 0.0
    for _ in range(NEWTON_STEPS):
        p = 1.0 / (1.0 + np.exp(-z @ beta))
        hessian = z.T @ (z * (p * (1 - p))[:, None]) + penalty
        beta = beta + np.linalg.solve(hessian, z.T @ (y - p) - penalty @ beta)
    raw = beta[1:] / sd
    return np.concatenate([[beta[0] - float(raw @ mean)], raw])


def _x(rows: list[dict], names: Iterable[str]) -> np.ndarray:
    logged = {"rank", "rookie_pick"}
    return np.array([[1.0, *(math.log(r[n]) if n in logged else r[n] for n in names)] for r in rows])


def _y(rows: list[dict], event: str = "lost") -> np.ndarray:
    return np.array([float(r[event]) for r in rows])


def _loo(rows: list[dict], names: tuple[str, ...], event: str = "lost", coefficient: bool = False) -> dict[str, float]:
    """Leave-season-out log loss and AUC of a logistic in `names` (none: the base rate)."""
    x, y = _x(rows, names), _y(rows, event)
    seasons = np.array([r["season"] for r in rows])
    p = np.zeros(len(rows))
    for season in sorted(set(seasons)):
        test = seasons == season
        p[test] = 1.0 / (1.0 + np.exp(-x[test] @ _logit(x[~test], y[~test])))
    p = np.clip(p, 1e-9, 1 - 1e-9)
    loss = -float(np.mean(np.where(y > 0, np.log(p), np.log(1 - p))))
    pos, neg = p[y > 0], p[y == 0]
    auc = float(np.mean((pos[:, None] > neg[None, :]) + 0.5 * (pos[:, None] == neg[None, :]))) if len(pos) else math.nan
    out = {"log_loss": round(loss, 5), "auc": round(auc, 4)}
    if coefficient:                              # the last covariate's coefficient on all seasons: + = more losses
        out["coefficient"] = round(float(_logit(x, y)[-1]), 4)
    return out


def _stored(rows: list[dict], names: tuple[str, ...], event: str = "lost") -> dict:
    beta = _logit(_x(rows, names), _y(rows, event))
    keys = {"rank": "market", "rookie_pick": "pick"}
    return {"intercept": round(float(beta[0]), 4), **{keys.get(n, n): round(float(b), 4) for n, b in zip(names, beta[1:])},
            "n": len(rows), "events": int(_y(rows, event).sum())}


def _candidates(session, seasons: Iterable[int], picks: list[dict]) -> dict[tuple[int, int], dict[str, float]]:
    """{(season, QB): candidate covariates at the season's start} (module docstring)."""
    from database.models import Player
    from services.sim_setup import first_seasons

    gsis = dict(session.execute(select(Player.gsis_id, Player.id)).all())
    drafted = {gsis[r["gsis_id"]]: (r["season"], r["pick"]) for r in picks if r["gsis_id"] in gsis}
    first = first_seasons(session)
    loaded = {s: replay_pool.load_season_data(session, s, SCORING) for s in range(min(seasons) - HISTORY, max(seasons))
              if s >= min(replay_pool.SUPPORTED_SEASONS)}
    started = {s: sim_qb.starters(data) for s, data in loaded.items()}
    out = {}
    for season in seasons:
        data = replay_pool.load_season_data(session, season, SCORING)
        experts = {pid: rank for (pid, week), rank in data.expert_ranks.items() if week == 0}
        window = [s for s in range(season - HISTORY, season) if s in loaded]
        weeks = sum(len({w for _, w in started[s]}) for s in window)
        starts, points = defaultdict(int), defaultdict(float)
        for s in window:
            for (_, week), pid in started[s].items():
                starts[pid] += 1
                points[pid] += loaded[s].actual.get((pid, week), 0.0)
        for pid, player in data.players.items():
            if player.position != "QB":
                continue
            year, pick = drafted.get(pid, (None, UNDRAFTED))
            n = starts.get(pid, 0)
            out[(season, pid)] = {
                "starts": math.log1p(n * HISTORY * FULL_SEASON / weeks) if weeks else 0.0,
                "points": (points.get(pid, 0.0) + FORM_POINTS) / (n + 1),
                "pick": math.log(pick), "years": float(season - (year or first.get(pid, season))),
                "expert": math.log(experts.get(pid, sim_qb.UNRANKED))}
    return out


def observations(session, seasons: list[int], picks: list[dict]) -> list[dict]:
    """Every decision row of the real `seasons` (sim_qb.walk), as dicts with the season, his name and `took`:
    a drafted rookie started when he lost the job."""
    from services import sim_qb_jobs, sim_setup
    from services.sim_next_year_fit import draft_class

    rows = []
    for season in seasons:
        data = replay_pool.load_season_data(session, season, SCORING)
        rookies = {pid: (r.team, r.pick) for pid, r in draft_class(session, season, picks).items() if r.position == "QB"}
        established = sim_qb_jobs.established_of(data, None, False, sim_setup.qb_inputs(session, season, picks))
        found, _ = sim_qb.walk(data, picks=rookies, established=established)
        names = {pid: p.name for pid, p in data.players.items()}
        rows += [{"season": season, "name": names.get(r.holder, str(r.holder)), "took": r.lost and r.starter in rookies,
                  **r.__dict__} for r in found]
        log.info("%s: %d rows, %d lost", season, len(found), sum(r.lost for r in found))
    return rows


def prior_rows(session, seasons: list[int]) -> list[tuple[float, bool]]:
    """[(mean of a QB's first projections, he started his first projected game)] (the `prior` fit)."""
    out = []
    for season in seasons:
        data = replay_pool.load_season_data(session, season, SCORING)
        started = set(sim_qb.starters(data).items())
        weeks: dict[int, list[tuple[int, float]]] = defaultdict(list)
        for (pid, week), points in data.projected.items():
            if pid in data.players and data.players[pid].position == "QB":
                weeks[pid].append((week, points))
        for pid, projected in weeks.items():
            early = sorted(projected)[:EARLY_PROJECTIONS]
            week = early[0][0]
            team = data.team_at(pid, week + 1)
            out.append((sum(p for _, p in early) / len(early), ((team, week), pid) in started))
    return out


def fit(session, seasons: list[int]) -> dict:
    """sim_qb.json's content (module docstring)."""
    from services.sim_draft_picks import draft_picks

    picks = draft_picks(range(1995, max(seasons) + 1))
    prior = prior_rows(session, seasons)
    p = _logit(np.array([[1.0, x] for x, _ in prior]), np.array([float(y) for _, y in prior]), ridge=0.0)
    start = {"intercept": round(float(p[0]), 4), "points": round(float(p[1]), 4), "n": len(prior)}
    rows = observations(session, seasons, picks)
    weekly = [r for r in rows if r["kind"] == "week" and not r["established"]]
    veterans = [r for r in weekly if r["rookie_pick"] is None]
    rookies = [r for r in weekly if r["rookie_pick"] is not None]
    back = [r for r in rows if r["kind"] == "return" and not r["established"]]
    hazard = _stored(veterans, MODELS["hazard"])
    z = _x(veterans, MODELS["hazard"]) @ np.array([hazard["intercept"], hazard["market"], hazard["form"], hazard["record"]])
    candidates = _candidates(session, seasons, picks)
    for r in veterans:
        r.update(candidates.get((r["season"], r["holder"]), {"starts": 0.0, "points": FORM_POINTS, "pick": math.log(UNDRAFTED),
                                                             "years": 0.0, "expert": math.log(sim_qb.UNRANKED)}))
    validation = {"hazard: none": _loo(veterans, ()), "hazard: market": _loo(veterans, ("rank",)),
                  "hazard: model": _loo(veterans, MODELS["hazard"]),
                  **{f"hazard: model + {name}": _loo(veterans, (*MODELS["hazard"], name), coefficient=True)
                     for name in ("starts", "points", "pick", "years", "expert")},
                  "rookie: none": _loo(rookies, (), "took"), "rookie: model": _loo(rookies, MODELS["rookie"], "took"),
                  "return: none": _loo(back, ()), "return: model": _loo(back, MODELS["return"])}
    losers = sorted((r for r in rows if r["lost"]), key=lambda r: (not r["established"], r["rank"], r["season"]))
    return {"seasons": seasons, "hazard": hazard, "rookie": _stored(rookies, MODELS["rookie"], "took"),
            "return": _stored(back, MODELS["return"]),
            "elevated": round(float(np.mean(1.0 / (1.0 + np.exp(-z)))), 5),
            "prior": start,
            "validation": validation,
            "losers": [f"{r['season']} wk{r['week']} {r['team']} {r['name']} (ADP rank {r['rank']}, {r['kind']}"
                       f"{', established' if r['established'] else ''})" for r in losers]}


# ---------------- the check ----------------

def _league(session, season: int, seed: int):
    """(the season a league on `season` would play with `seed`, its first simulated week, its established QBs, its
    rookie ids, seconds the simulation alone took)."""
    from services import sim_next_year, sim_qb_jobs, sim_season, sim_setup, sim_world
    from services.replay_state import Settings

    kind, through = sim_setup.kind_of(session, season)
    settings = Settings(season=season, sim_from=1 if kind == sim_setup.FUTURE else through + 1)
    if kind == sim_setup.FUTURE:
        base, world, carried, qbs, _ = sim_setup._future_base(session, season, settings, seed)
        rookies = [pid for pid in world.players if pid >= sim_next_year.ROOKIE_ID_BASE]
        started = time.perf_counter()
        data = sim_season.simulate(base, seed, 1, prior=world, carried=carried, rookies=rookies, qbs=qbs)
        took = time.perf_counter() - started
        return data, 1, sim_qb_jobs.established_of(base, sim_world.estimate(base, 1, world), True, qbs), set(rookies), took
    base = sim_setup.with_status_now(session, replay_pool.load_season_data(session, season, settings.scoring,
                                                                           through=through), through + 1)
    rookies = [pid for pid, first in sim_setup.first_seasons(session).items() if first == season]
    qbs = sim_setup.qb_inputs(session, season)
    started = time.perf_counter()
    data = sim_season.simulate(base, seed, through + 1, rookies=rookies, qbs=qbs)
    took = time.perf_counter() - started
    return data, through + 1, sim_qb_jobs.established_of(base, None, False, qbs), set(rookies), took


def _check_seed(seed: int, season: int) -> dict[str, float]:
    from database.db import SessionLocal
    from services import sim_metrics

    with SessionLocal() as session:
        data, first, established, rookies, took = _league(session, season, seed)
        base = replay_pool.load_season_data(session, season, SCORING, through=first - 1) if first > 1 else None
    out = {**sim_qb.job_metrics(data, first, established), "simulate seconds": took,
           "QB rookie later starters": sim_metrics.rookies(data, rookies)["rookie QB later starters"]}
    if base is not None:
        holders = sim_qb.walk(base.as_of(first), causal=True)[1]
        mine = sim_qb.starters(data)
        teams = [team for team, (pid, _) in holders.items() if (pid, first) not in data.injuries]   # healthy now
        out["QB first-week starter not the healthy holder"] = float(   # who did not play (no in-game exit)
            sum(1 for team in teams if (team, first) in mine and mine[(team, first)] != holders[team][0]
                and (holders[team][0], first) not in data.box))
    return out


def check(season: int, seeds: int, workers: int) -> dict[str, float]:
    """The mean of _check_seed over seeds 1..seeds, plus the totals of top-12 and established job losses."""
    run = partial(_check_seed, season=season)
    if workers <= 1:
        results = [run(seed) for seed in range(1, seeds + 1)]
    else:
        with ProcessPoolExecutor(workers) as pool:
            results = list(pool.map(run, range(1, seeds + 1)))
    report = {key: float(np.nanmean([r[key] for r in results])) for key in results[0]}
    for key in ("QB top-12 lost job", "QB established lost job"):
        report[f"{key} (total)"] = float(sum(r[key] for r in results))
    return report


def _seasons(text: str) -> list[int]:
    first, _, last = text.partition("-")
    return list(range(int(first), int(last or first) + 1))


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fit", action="store_true", help="measure and write sim_qb.json")
    parser.add_argument("--seasons", default="2019-2025")
    parser.add_argument("--check", action="store_true", help="simulate --seeds leagues on --season and report")
    parser.add_argument("--season", type=int, default=2027)
    parser.add_argument("--seeds", type=int, default=200)
    parser.add_argument("--workers", type=int, default=min(3, os.cpu_count() or 1))
    args = parser.parse_args(argv)
    if args.fit:
        from database.db import SessionLocal

        with SessionLocal() as session:
            fitted = fit(session, _seasons(args.seasons))
        sim_qb.PATH.write_text(json.dumps(fitted, indent=1) + "\n")
        sim_qb.stored.cache_clear()
        log.info("wrote %s: %s", sim_qb.PATH.name, json.dumps({k: v for k, v in fitted.items() if k != "losers"}))
        for line in fitted["losers"]:
            log.info("  lost the job healthy: %s", line)
    elif args.check:
        for key, value in check(args.season, args.seeds, args.workers).items():
            log.info("%-48s %8.3f", key, value)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
