"""Fit the pregame line model of simulated games (services/sim_lines.py) on real seasons.

    python -m services.sim_lines_fit --fit [--seasons 2018-2025] [--refresh]    # writes sim_lines.json
    python -m services.sim_lines_fit [--seasons 2018-2025]                      # the report only

For every real regular-season game: the inputs sim_lines uses, computed exactly as a simulated season computes
them (each week's public belief, sim_world.estimate(data, week) with no prior World as in a live or mid-season
league, its hard-tagged players out; the team ratings from the games before the week; last season's points
ratings carried over), next to the Vegas closing line and the result. Read-only on the database; the per-game
inputs are cached in backend/data/cache/sim_lines_games.json (git-ignored; --refresh rebuilds them).

Leave-season-out: for each knob setting and each held-out season, the league level and home edge the ratings
use and the coefficients are fitted on the other seasons only (least squares against the Vegas line, less noisy
than results) and judged on the held-out one. The rating knobs themselves (decay, pseudo-games, carry-over: a
small grid) are picked on that held-out RMSE against Vegas without a nested split, so the reported held-out
numbers are slightly optimistic about the knob choice (the grid's settings differ by under 0.01 in held-out
correlation with the results). The report gives, on held-out seasons, the
correlation with the real margin and total next to Vegas's, the spread of predicted margins, the home edge,
blowout shares in the most lopsided and closest fifth, and what each input adds (held-out correlation with the
real margin / total when it is dropped). The coefficients written are fitted on every season.

Inputs tried and dropped (held out, 2018-2025, all weeks; with them / without): yards per play gained and allowed
(opponent-adjusted like the points: margin r 0.390 / 0.390, total r 0.234 / 0.234), team EPA per play from
team_game_stats (+0.001 margin r; and simulated games have none), the past weeks' Vegas lines as a market rating
(+0.015 margin r on real seasons, but a simulated season's past lines are its own), a dome flag (+0.012 total r;
the schedule carries no roof), capping blowout margins in the ratings (no gain).

`yards_power`: the slope of log yards on log points over real team games (box-score yards), how far a side's
yards move when sim_game centres its points.
"""
import argparse
import itertools
import json
import logging
from collections import defaultdict
from pathlib import Path

import numpy as np

from services import sim_lines

log = logging.getLogger(__name__)

CACHE = Path(__file__).resolve().parent.parent / "data" / "cache" / "sim_lines_games.json"
SCORING = "half"
INPUTS = ("points", "belief", "qb_level", "qb_change", "rest")
TOTAL_INPUTS = ("points", "belief", "qb_level", "qb_change")
GRID = {"decay": (0.9, 0.95, 1.0), "prior_games": (4.0, 8.0, 12.0), "carry": (0.5, 0.7, 0.85)}
LEVEL_GAMES = 64.0               # pseudo team-games holding the league level (about four weeks of games)
BLOWOUT = 21


def _seasons(text: str) -> list[int]:
    first, _, last = text.partition("-")
    return list(range(int(first), int(last or first) + 1))


def _season_rows(session, season: int) -> dict:
    """The cached inputs of one real season: its games (with belief points, rest, Vegas, result), its scores,
    last season's scores and (points, box-score yards) of each team game."""
    from services import replay_pool, sim_world
    from services.schedule_strength import load_schedule

    data = replay_pool.load_season_data(session, season, SCORING)
    games = []
    for week in range(1, data.last_week + 1):
        known = data.as_of(week)
        belief = sim_world.estimate(data, week)
        out = {pid for (pid, w), tag in data.injuries.items() if w == week and tag.hard}
        for g in data.schedule.games:
            if g.week != week or g.home_team not in belief.teams or g.away_team not in belief.teams:
                continue
            hs, as_ = data.scores.get((g.home_team, week)), data.scores.get((g.away_team, week))
            if hs is None or as_ is None or g.spread_line is None or g.total_line is None:
                continue
            (bh, ba), quality = sim_lines.summary(belief, g.home_team, g.away_team, out)
            qbs = [(q, belief.teams[team].passer) for q, team in zip(quality, (g.home_team, g.away_team))]
            games.append({"week": week, "home": g.home_team, "away": g.away_team, "belief": [bh, ba], "qbs": qbs,
                          "rest": [sim_lines.rest_days(known, g.home_team, week),
                                   sim_lines.rest_days(known, g.away_team, week)],
                          "vegas": [g.spread_line, g.total_line], "score": [hs, as_]})
    previous = [(g.week, g.home_team, g.away_team, g.home_score, g.away_score)
                for g in load_schedule(session, season - 1).games if g.is_final]
    seen = sim_world._seen(data, data.last_week + 1)
    yards = [(data.scores[key], seen.pass_yards[key] + sum(line.rush_yds for line in lines.values()))
             for key, lines in seen.games.items() if key in data.scores]
    return {"games": games, "scores": sim_lines.scores(data, data.last_week + 1), "previous": previous,
            "yards": yards}


def load(seasons: list[int], refresh: bool = False) -> dict[int, dict]:
    from database.db import SessionLocal

    cached = json.loads(CACHE.read_text()) if CACHE.exists() and not refresh else {}
    missing = [s for s in seasons if str(s) not in cached]
    if missing:
        with SessionLocal() as session:
            for season in missing:
                log.info("Building inputs for %s", season)
                cached[str(season)] = _season_rows(session, season)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cached))
    return {s: cached[str(s)] for s in seasons}


def _knobs(decay: float, prior_games: float, carry: float, levels: dict) -> dict:
    return {"decay": decay, "prior_games": prior_games, "carry": carry, "level_games": LEVEL_GAMES, **levels}


def _levels(data: dict[int, dict]) -> dict:
    """The long-run league level (points per team game) and home edge over these seasons."""
    games = [g for season in data.values() for g in season["scores"]]
    home, away = np.array([g[3] for g in games], dtype=float), np.array([g[4] for g in games], dtype=float)
    return {"level": round(float(np.concatenate([home, away]).mean()), 4), "home": round(float((home - away).mean()), 4)}


def folds(data: dict[int, dict], combo: tuple) -> dict[int, dict[int, dict[str, np.ndarray]]]:
    """{held-out season: features of every season with the league level and home edge of the others only}."""
    return {held: features(data, _knobs(*combo, _levels({s: d for s, d in data.items() if s != held})))
            for held in data}


def features(data: dict[int, dict], knobs: dict) -> dict[int, dict[str, np.ndarray]]:
    """{season: {input: (games, 2) margin and total parts, plus vegas, score, week}}."""
    out = {}
    for season, rows in data.items():
        games = [tuple(g) for g in rows["scores"]]
        prior = sim_lines.carry_over([tuple(x) for x in rows["previous"]], knobs) if rows["previous"] else None
        rated = {week: sim_lines.ratings(games, week, prior, knobs) for week in {g["week"] for g in rows["games"]}}
        parts = defaultdict(list)
        for g in rows["games"]:
            found = sim_lines.features(rated[g["week"]], g["home"], g["away"], tuple(g["belief"]), tuple(g["rest"]),
                                       tuple(tuple(q) for q in g["qbs"]))
            for name, value in found.items():
                parts[name].append(value)
            parts["vegas"].append(g["vegas"])
            parts["score"].append((g["score"][0] - g["score"][1], g["score"][0] + g["score"][1]))
            parts["week"].append((g["week"], 0))
        out[season] = {name: np.array(values, dtype=float) for name, values in parts.items()}
    return out


def _design(f: dict[str, np.ndarray], inputs: tuple[str, ...], col: int) -> np.ndarray:
    return np.column_stack([np.ones(len(f["vegas"]))] + [f[name][:, col] for name in inputs])


def _fit(feats: list[dict], inputs: tuple[str, ...], col: int) -> np.ndarray:
    x = np.vstack([_design(f, inputs, col) for f in feats])
    y = np.concatenate([f["vegas"][:, col] for f in feats])
    return np.linalg.lstsq(x, y, rcond=None)[0]


def held_out(by_fold: dict[int, dict[int, dict]], inputs: dict[int, tuple[str, ...]]) -> dict[int, np.ndarray]:
    """{season: (games, 2) held-out predicted margin and total}; `by_fold`: `folds`; inputs: {column: inputs used}."""
    out = {}
    for season, feats in by_fold.items():
        rest = [feats[s] for s in feats if s != season]
        pred = [_design(feats[season], inputs[col], col) @ _fit(rest, inputs[col], col) for col in (0, 1)]
        out[season] = np.column_stack(pred)
    return out


def _own(by_fold: dict[int, dict[int, dict]]) -> dict[int, dict]:
    """Each season's features as its own fold computed them (for the held-out reports)."""
    return {season: feats[season] for season, feats in by_fold.items()}


def _report(feats: dict[int, dict], pred: dict[int, np.ndarray], weeks: tuple[int, int] = (1, 18)) -> dict:
    mask = {s: (f["week"][:, 0] >= weeks[0]) & (f["week"][:, 0] <= weeks[1]) for s, f in feats.items()}
    p = np.vstack([pred[s][mask[s]] for s in feats])
    v = np.vstack([feats[s]["vegas"][mask[s]] for s in feats])
    r = np.vstack([feats[s]["score"][mask[s]] for s in feats])
    corr = lambda a, b: float(np.corrcoef(a, b)[0, 1])  # noqa: E731

    def fifths(m: np.ndarray) -> tuple[float, float]:
        big, small = np.abs(m) >= np.quantile(np.abs(m), 0.8), np.abs(m) <= np.quantile(np.abs(m), 0.2)
        return float((np.abs(r[big, 0]) >= BLOWOUT).mean()), float((np.abs(r[small, 0]) >= BLOWOUT).mean())

    return {"games": len(p), "r margin": corr(p[:, 0], r[:, 0]), "vegas r margin": corr(v[:, 0], r[:, 0]),
            "r total": corr(p[:, 1], r[:, 1]), "vegas r total": corr(v[:, 1], r[:, 1]),
            "r vs vegas margin": corr(p[:, 0], v[:, 0]), "r vs vegas total": corr(p[:, 1], v[:, 1]),
            "sd margin": float(p[:, 0].std()), "vegas sd margin": float(v[:, 0].std()),
            "home edge": float(p[:, 0].mean()), "vegas home edge": float(v[:, 0].mean()),
            "real home edge": float(r[:, 0].mean()), "resid sd": float((r[:, 0] - p[:, 0]).std()),
            "vegas resid sd": float((r[:, 0] - v[:, 0]).std()),
            "21+ in lopsided fifth": fifths(p[:, 0])[0], "21+ in closest fifth": fifths(p[:, 0])[1],
            "vegas 21+ in lopsided fifth": fifths(v[:, 0])[0], "vegas 21+ in closest fifth": fifths(v[:, 0])[1]}


def _rmse(feats: dict[int, dict], pred: dict[int, np.ndarray]) -> float:
    err = np.vstack([pred[s] - feats[s]["vegas"] for s in feats])
    return float(np.sqrt((err ** 2).mean(axis=0)).sum())


def yards_power(data: dict[int, dict]) -> float:
    """The slope of log yards on log points over real team games with points: how much a side's yards move when
    its expected points do (sim_game._scaled)."""
    pts, yds = [], []
    for season in data.values():
        for points, yards in season["yards"]:
            if points > 0 and yards > 0:
                pts.append(np.log(points))
                yds.append(np.log(yards))
    pts, yds = np.array(pts), np.array(yds)
    return float(np.cov(pts, yds)[0, 1] / pts.var(ddof=1))


def fit(seasons: list[int], refresh: bool = False) -> tuple[dict, dict]:
    """(sim_lines.json contents, report)."""
    data = load(seasons, refresh)
    inputs = {0: INPUTS, 1: TOTAL_INPUTS}
    best = None
    for combo in itertools.product(*GRID.values()):
        by_fold = folds(data, combo)
        score = _rmse(_own(by_fold), held_out(by_fold, inputs))
        log.info("knobs %s: held-out RMSE vs Vegas (margin + total) %.3f", combo, score)
        if best is None or score < best[0]:
            best = (score, combo, by_fold)
    _, combo, by_fold = best
    own, pred = _own(by_fold), held_out(by_fold, inputs)
    report = {"knobs": combo, "all weeks": _report(own, pred), "weeks 5-18": _report(own, pred, (5, 18)),
              "dropped": {}}
    for name in INPUTS:
        fewer = {col: tuple(i for i in inputs[col] if i != name) for col in (0, 1)}
        fewer_pred = held_out(by_fold, fewer)
        dropped = _report(own, fewer_pred)
        report["dropped"][name] = {"r margin": dropped["r margin"], "r total": dropped["r total"],
                                   "RMSE vs Vegas": _rmse(own, fewer_pred)}
    report["RMSE vs Vegas"] = _rmse(own, pred)
    report["2023-2025 weeks 5-18"] = _report({s: own[s] for s in own if s >= 2023}, pred, (5, 18))
    knobs = _knobs(*combo, _levels(data))                  # the model written: every season
    feats = features(data, knobs)
    coefs = {col: _fit(list(feats.values()), inputs[col], col) for col in (0, 1)}
    model = {"seasons": seasons, "ratings": knobs, "yards_power": round(yards_power(data), 4),
             "margin": {"const": round(float(coefs[0][0]), 4), **{n: round(float(b), 4) for n, b in zip(INPUTS, coefs[0][1:])}},
             "total": {"const": round(float(coefs[1][0]), 4),
                       **{n: round(float(b), 4) for n, b in zip(TOTAL_INPUTS, coefs[1][1:])}}}
    return model, report


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seasons", default="2018-2025")
    parser.add_argument("--fit", action="store_true", help="write sim_lines.json")
    parser.add_argument("--refresh", action="store_true", help="rebuild the cached per-game inputs")
    args = parser.parse_args(argv)
    model, report = fit(_seasons(args.seasons), args.refresh)
    log.info("%s", json.dumps(report, indent=1))
    log.info("%s", json.dumps(model, indent=1))
    if args.fit:
        sim_lines.write(model)


if __name__ == "__main__":
    main()
