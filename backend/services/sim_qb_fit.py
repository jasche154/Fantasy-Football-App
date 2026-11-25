"""Simulated seasons: fit sim_qb.json (who holds the QB job, services/sim_qb.py) on real seasons, and check it.

    python -m services.sim_qb_fit --fit [--seasons 2019-2025]
    python -m services.sim_qb_fit --check --season 2027 [--seeds 200] [--workers 3]

--fit: each real season's starters are its attempts leaders per game (player_stats_weekly, regular season), walked
with sim_qb.walk on the season's data (injury tags, projections, ADP, scores; half PPR). Fitted on the rows:
- `hazard`: logistic (ridge RIDGE, the intercept free) of a healthy job loss on the "week" rows: market (log
  market rank), form, record;
- `return`: logistic of the fill-in keeping the job on the "return" rows, in log market rank;
- `established`: the security of the most secure real holder who lost a job healthy (either kind of row);
- `elevated`: the mean real weekly hazard of the holders outside the established range (sim_qb_jobs: a job-in-
  danger rumour only above it);
- `prior`: logistic of a QB starting his first projected game in his first projection (sim_world.implied_role:
  the starts weight a projection implies, no cliff);
- `validation`: leave-season-out log loss and AUC of the hazard: no covariate, market only, the model, and the
  model plus each candidate that is not in it (starts over the last three seasons on any team, scaled to three
  full seasons when fewer are in the database (2018 on); points a start over them; nflverse draft pick and years
  since that draft (an undrafted QB: pick UNDRAFTED, years unknown = his seasons in the database, an undercount
  for a veteran from before 2018); the preseason expert rank, 2020 on); and `losers`: the real holders who lost
  the job healthy, most secure first.
--check: builds `seeds` seasons the way a league would (sim_setup: the future season simulated whole, or the live
one from its first unplayed week) and logs sim_qb.job_metrics per season (mean over seeds, plus the total top-12
job losses) and the seconds simulate() took, and for a live season how often the first simulated week's starter is not the holder of the real
weeks while that holder is healthy and does not play.
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
RIDGE = 1.0                      # ridge penalty on each hazard coefficient (31 real losses: keep it small and stable)
NEWTON_STEPS = 50
UNDRAFTED = 300                  # draft pick of an undrafted QB (the candidate covariate)
HISTORY = 3                      # seasons of starts history in the candidate covariates
FULL_SEASON = 17
EARLY_PROJECTIONS = 3            # as sim_world.EARLY_PROJECTIONS


def real_starters(session, season: int) -> dict[tuple[str, int], int]:
    """{(team, week): QB with the most pass attempts} of the season's regular-season games (team from game_id)."""
    from database.models import PlayerStatsWeekly as Stats

    best: dict[tuple[str, int], tuple[int, int]] = {}
    for pid, week, game, opponent, attempts in session.execute(
            select(Stats.player_id, Stats.week, Stats.game_id, Stats.opponent_team, Stats.attempts)
            .where(Stats.season == season, Stats.season_type == "REG", Stats.attempts > 0)):
        away, home = game.split("_")[2:4]
        team = home if opponent == away else away
        if attempts > best.get((team, week), (0, 0))[1]:
            best[(team, week)] = (pid, attempts)
    return {key: pid for key, (pid, _) in best.items()}


def _logit(x: np.ndarray, y: np.ndarray, ridge: float = RIDGE) -> np.ndarray:
    """Ridge logistic regression (column 0 is the unpenalized intercept), Newton's method."""
    beta = np.zeros(x.shape[1])
    beta[0] = math.log(max(y.mean(), 1e-6) / max(1 - y.mean(), 1e-6))
    penalty = ridge * np.eye(x.shape[1])
    penalty[0, 0] = 0.0
    for _ in range(NEWTON_STEPS):
        p = 1.0 / (1.0 + np.exp(-x @ beta))
        hessian = x.T @ (x * (p * (1 - p))[:, None]) + penalty
        beta = beta + np.linalg.solve(hessian, x.T @ (y - p) - penalty @ beta)
    return beta


def _hazard_x(rows: list[dict], extra: tuple[str, ...] = ()) -> np.ndarray:
    return np.array([[1.0, math.log(r["rank"]), r["form"], r["record"], *(r[name] for name in extra)] for r in rows])


def _loo(rows: list[dict], extra: tuple[str, ...] = (), columns: int | None = None) -> dict[str, float]:
    """Leave-season-out log loss and AUC of the hazard on the first `columns` model columns plus `extra`."""
    x = _hazard_x(rows, extra)
    keep = list(range(columns or 4)) + list(range(4, 4 + len(extra)))
    x = x[:, keep]
    y = np.array([float(r["lost"]) for r in rows])
    seasons = np.array([r["season"] for r in rows])
    p = np.zeros(len(rows))
    for season in sorted(set(seasons)):
        test = seasons == season
        p[test] = 1.0 / (1.0 + np.exp(-x[test] @ _logit(x[~test], y[~test])))
    loss = -float(np.mean(np.where(y > 0, np.log(p), np.log(1 - p))))
    pos, neg = p[y > 0], p[y == 0]
    auc = float(np.mean((pos[:, None] > neg[None, :]) + 0.5 * (pos[:, None] == neg[None, :]))) if len(pos) else math.nan
    out = {"log_loss": round(loss, 5), "auc": round(auc, 4)}
    if extra:                                    # its coefficient on all seasons: + = more job losses
        out["coefficient"] = round(float(_logit(x, y)[-1]), 4)
    return out


def _candidates(session, seasons: Iterable[int], picks: list[dict]) -> dict[tuple[int, int], dict[str, float]]:
    """{(season, QB): candidate covariates at the season's start} (module docstring)."""
    from database.models import Player

    gsis = dict(session.execute(select(Player.gsis_id, Player.id)).all())
    drafted = {gsis[r["gsis_id"]]: (r["season"], r["pick"]) for r in picks if r["gsis_id"] in gsis}
    from services.sim_setup import first_seasons

    first = first_seasons(session)
    loaded = {s: real_starters(session, s) for s in range(min(seasons) - HISTORY, max(seasons))
              if s >= min(replay_pool.SUPPORTED_SEASONS)}
    out = {}
    for season in seasons:
        data = replay_pool.load_season_data(session, season, SCORING)
        experts = {pid: rank for (pid, week), rank in data.expert_ranks.items() if week == 0}
        window = [s for s in range(season - HISTORY, season) if s in loaded]
        weeks = sum(len({w for _, w in loaded[s]}) for s in window)
        starts, points = defaultdict(int), defaultdict(float)
        for s in window:
            history = replay_pool.load_season_data(session, s, SCORING)
            for (_, week), pid in loaded[s].items():
                starts[pid] += 1
                points[pid] += history.actual.get((pid, week), 0.0)
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


FORM_POINTS = 12.0               # points a start a QB with no history is shrunk toward (candidate covariate)


def observations(session, seasons: list[int]) -> list[dict]:
    """Every decision row of the real `seasons` (sim_qb.walk on attempts leaders), as dicts with the season."""
    rows = []
    for season in seasons:
        data = replay_pool.load_season_data(session, season, SCORING)
        found, _ = sim_qb.walk(data, real_starters(session, season))
        names = {pid: p.name for pid, p in data.players.items()}
        rows += [{"season": season, "name": names.get(r.holder, str(r.holder)), **r.__dict__} for r in found]
        log.info("%s: %d rows, %d lost", season, len(found), sum(r.lost for r in found))
    return rows


def prior_rows(session, seasons: list[int]) -> list[tuple[float, bool]]:
    """[(mean of a QB's first projections, he started his first projected game)] (the `prior` fit)."""
    out = []
    for season in seasons:
        data = replay_pool.load_season_data(session, season, SCORING)
        started = set(real_starters(session, season).items())
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

    rows = observations(session, seasons)
    weekly = [r for r in rows if r["kind"] == "week"]
    back = [r for r in rows if r["kind"] == "return"]
    a, market, form, record = _logit(_hazard_x(weekly), np.array([float(r["lost"]) for r in weekly]))
    ret = _logit(np.array([[1.0, math.log(r["rank"])] for r in back]), np.array([float(r["lost"]) for r in back]))
    hazard = {"intercept": round(float(a), 4), "market": round(float(market), 4), "form": round(float(form), 4),
              "record": round(float(record), 4), "n": len(weekly), "lost": sum(r["lost"] for r in weekly)}
    losers = sorted((r for r in rows if r["lost"]), key=lambda r: (r["rank"], r["season"]))
    established = -hazard["market"] * math.log(losers[0]["rank"])
    secure = [r for r in weekly if -hazard["market"] * math.log(r["rank"]) < established]
    z = _hazard_x(secure) @ np.array([a, market, form, record])
    prior = prior_rows(session, seasons)
    p = _logit(np.array([[1.0, x] for x, _ in prior]), np.array([float(y) for _, y in prior]), ridge=0.0)
    candidates = _candidates(session, seasons, draft_picks(range(1995, max(seasons) + 1)))
    for r in weekly:
        r.update(candidates.get((r["season"], r["holder"]), {"starts": 0.0, "points": FORM_POINTS, "pick": math.log(UNDRAFTED),
                                                              "years": 0.0, "expert": math.log(sim_qb.UNRANKED)}))
    names = ("starts", "points", "pick", "years", "expert")
    validation = {"none": _loo(weekly, columns=1), "market": _loo(weekly, columns=2), "model": _loo(weekly),
                  **{f"model + {name}": _loo(weekly, (name,)) for name in names}}
    return {"seasons": seasons, "hazard": hazard,
            "return": {"intercept": round(float(ret[0]), 4), "market": round(float(ret[1]), 4), "n": len(back),
                       "lost": sum(r["lost"] for r in back)},
            "established": math.floor(established * 1e4) / 1e4,    # rounded down: his own rank stays inside
            "established_rank": losers[0]["rank"],
            "elevated": round(float(np.mean(1.0 / (1.0 + np.exp(-z)))), 5),
            "prior": {"intercept": round(float(p[0]), 4), "points": round(float(p[1]), 4), "n": len(prior)},
            "validation": validation,
            "losers": [f"{r['season']} wk{r['week']} {r['team']} {r['name']} (rank {r['rank']}, {r['kind']})" for r in losers]}


# ---------------- the check ----------------

def _league(session, season: int, seed: int):
    """(the season a league on `season` would play with `seed`, its first simulated week, its base, seconds the
    simulation alone took)."""
    from services import sim_next_year, sim_season, sim_setup
    from services.replay_state import Settings

    kind, through = sim_setup.kind_of(session, season)
    settings = Settings(season=season, sim_from=1 if kind == sim_setup.FUTURE else through + 1)
    if kind == sim_setup.FUTURE:
        base, world, carried = sim_setup._future_base(session, season, settings, seed)
        rookies = [pid for pid in world.players if pid >= sim_next_year.ROOKIE_ID_BASE]
        started = time.perf_counter()
        data = sim_season.simulate(base, seed, 1, prior=world, carried=carried, rookies=rookies)
        return data, 1, base, time.perf_counter() - started
    base = replay_pool.load_season_data(session, season, settings.scoring, through=through)
    base = sim_setup.with_status_now(session, base, through + 1)
    rookies = [pid for pid, first in sim_setup.first_seasons(session).items() if first == season]
    started = time.perf_counter()
    data = sim_season.simulate(base, seed, through + 1, rookies=rookies)
    return data, through + 1, base, time.perf_counter() - started


def _check_seed(seed: int, season: int) -> dict[str, float]:
    from database.db import SessionLocal

    with SessionLocal() as session:
        data, first, base, took = _league(session, season, seed)
    out = {**sim_qb.job_metrics(data, first), "simulate seconds": took}
    if first > 1:
        holders = sim_qb.walk(base.as_of(first), causal=True)[1]
        teams = [team for team, (pid, _) in holders.items() if (pid, first) not in data.injuries]   # healthy now
        mine = sim_qb.starters(data)
        out["QB first-week starter not the healthy holder"] = float(   # who did not play (no in-game exit)
            sum(1 for team in teams if (team, first) in mine and mine[(team, first)] != holders[team][0]
                and (holders[team][0], first) not in data.box))
    return out


def check(season: int, seeds: int, workers: int) -> dict[str, float]:
    """The mean of _check_seed over seeds 1..seeds, plus the total of top-12 job losses."""
    run = partial(_check_seed, season=season)
    if workers <= 1:
        results = [run(seed) for seed in range(1, seeds + 1)]
    else:
        with ProcessPoolExecutor(workers) as pool:
            results = list(pool.map(run, range(1, seeds + 1)))
    report = {key: float(np.nanmean([r[key] for r in results])) for key in results[0]}
    report["QB top-12 lost job (total)"] = float(sum(r["QB top-12 lost job"] for r in results))
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
