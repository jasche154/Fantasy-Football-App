"""Simulated seasons: fit how real teams' play calling follows the score and how they share work (writes sim_script.json).

    python -m services.sim_script_fit --fit [--seasons 2018-2025] [--refresh]

Read-only on the database (the participation fit reads the public belief, sim_world.estimate). Run it before
`python -m services.sim_closing_fit --fit`, whose ending table reads the window's play counts from the same cache.

A real game is cut in three parts, the way sim_draw draws one: the first half, the second half up to the closing
window's cut (sim_closing_fit.WINDOW, ten minutes before the end of regulation) and the window itself. Per team and
part, from nflverse play-by-play (regular season, overtime and two-point tries left out, plays wiped by a penalty
too), cached in SEGMENTS_CACHE (git-ignored; the network is needed the first time, with --refresh, or for a season
not in it): the margin entering the part, targets (pass attempts with a receiver), pass attempts and sacks, carries
(every rush attempt: a kneel-down is a quarterback's carry and a scramble too, as the box score counts them), the
starting quarterback's carries, kneels, scrambles and spikes. The closing window's counts ride on sim_closing.json's
real endings (sim_closing_fit joins them); this module fits the two halves before it:

- `shares`: each part's share of a team's targets and of its carries (2018-2025: 50 / 30 / 20%).
- `first_half`: the relative change of a team's first-half targets and carries per point of its pregame expected
  margin (the Vegas spread), within each team-season (each one's own mean taken out, so a team's style is not
  counted): favourites run slightly more plays.
- `second_half`: the same for the second half before the cut, per point of the spread and per point of the margin
  at half time (per point of lead: targets -1.1%, carries +0.65%, so a team down 14 after the half throws about
  16% more and runs about 9% less). Linear in the margin: on held-out seasons a clip at 21 points is a hair better
  for targets (RMSE 0.32402 against 0.32422) and worse for carries (0.37385 against 0.37377), 10 and 14 worse for
  both (`check`), so one linear slope for both.
- `participation`: by position (RB, WR, TE), how often a player is in the game plan, by the touches (targets +
  carries) the public belief expects of him in a game: q = 1 - c exp(-touches / scale). Real depth players are
  lumpy, no touch in many games and several in a few, backs most of all: a healthy back the belief expects 3-4
  touches of has none in 46% of his games, a receiver in 17%, where a draw around their means would leave them
  without one in about 3%. Fitted by maximum likelihood on whether a healthy player had a touch at all, the belief
  before weeks 6, 10 and 14 against the next four weeks, 2019-2025, above a floor of untagged no-shows (the zero
  share of the position's players expected 8+ touches; the simulator has its own); in the game plan a player's
  work is his weight / q, so his mean and every teammate's stay the belief's (sim_script.plan_weights). Held-out
  seasons score better with it (`check`: the mean log loss with and without).
- `spread`: a team's pace for the day beyond a Poisson's spread of its plays (within a team-season its plays before
  the cut spread 6.8 a game, a Poisson's 6.9: none) and its pass share's beyond a binomial's (a first half's pass
  share spreads beyond a binomial's by sd 0.027 within team-seasons: a pass-first or run-first plan).
- `qb_share`: a league-wide factor on a quarterback's full-strength share of his team's carries (sim_game
  ._carry_weights): the share counts every dressed player, though real games see only some of them, so it runs
  short; fitted as his real carries over his share x his team's real carries in the games he started after each of
  PART_WEEKS, checked held out by season and by share size and dressed backfield size (`qb_share`).
- `league`: mean targets and carries per team-game of the same games (the window's real counts are scaled by a
  team's own volume over these), the mean yards of a kneel-down.
"""
import argparse
import json
import math
import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path

import numpy as np

from services.sim_script_segments import (  # noqa: F401  (re-exported: the counts keep their import path)
    COUNTS, CUT, SEGMENTS_CACHE, atomic_write, game_counts, segments, window_counts,
)

log = logging.getLogger(__name__)

SCRIPT_PATH = Path(__file__).with_name("sim_script.json")
SEASONS = range(2018, 2026)
PART_WEEKS = (6, 10, 14)         # the belief before each of these weeks is judged on the next WEEKS_AHEAD weeks
WEEKS_AHEAD = 4
FLOOR_TOUCHES = 8.0              # players expected this many touches set the no-show floor
FLOOR_ROWS = 200                 # ... when a position has this many player-games of them (tight ends: 45)
CLIPS = (None, 21, 14, 10)       # second-half margin clips compared on held-out seasons
GRID_C = np.arange(0.30, 1.0001, 0.02)
GRID_SCALE = np.arange(0.5, 12.0, 0.25)


# ---------------- the segment counts (cached) ----------------

# ---------------- the fits ----------------

def _rows(games: Iterable[Mapping]) -> np.ndarray:
    """One row per team-game: season, team-season id, spread, then each part's COUNTS."""
    ids: dict[tuple, int] = {}
    out = []
    for g in games:
        for team, sign in ((g["home"], 1.0), (g["away"], -1.0)):
            key = ids.setdefault((g["season"], team), len(ids))
            out.append([g["season"], key, sign * g["spread"], *np.ravel(g[team])])
    return np.array(out, dtype=float)


def _col(rows: np.ndarray, part: int, name: str) -> np.ndarray:
    return rows[:, 3 + part * len(COUNTS) + COUNTS.index(name)]


def _within(x: np.ndarray, group: np.ndarray) -> np.ndarray:
    """`x` less its mean within each group."""
    means = np.bincount(group, weights=x) / np.maximum(np.bincount(group), 1)
    return x - means[group]


def _relative(y: np.ndarray, group: np.ndarray) -> np.ndarray:
    """`y` over its mean within each group (1 for a group whose mean is 0)."""
    means = np.bincount(group, weights=y) / np.maximum(np.bincount(group), 1)
    return np.where(means[group] > 0, y / np.where(means[group] > 0, means[group], 1.0), 1.0)


def halves(games: list[Mapping]) -> dict:
    """`shares`, `first_half`, `second_half` and their held-out `check` (see the module docstring)."""
    rows = _rows(games)
    group = rows[:, 1].astype(int)
    spread = _within(rows[:, 2], group)
    margin = _within(_col(rows, 1, "margin"), group)
    out: dict = {"shares": {}, "first_half": {}, "second_half": {}, "check": {}}
    for stat in ("targets", "carries"):
        totals = [float(_col(rows, part, stat).mean()) for part in range(3)]
        out["shares"][stat] = [round(t / sum(totals), 4) for t in totals]
        first = _relative(_col(rows, 0, stat), group) - 1.0
        out["first_half"][stat] = round(float(spread @ first / (spread @ spread)), 5)
        second = _relative(_col(rows, 1, stat), group) - 1.0
        x = np.column_stack([spread, margin])
        b = np.linalg.lstsq(x, second, rcond=None)[0]
        out["second_half"][stat] = {"spread": round(float(b[0]), 5), "margin": round(float(b[1]), 5)}
        out["check"][stat] = _clip_check(rows[:, 0], spread, margin, second)
    return out


def _clip_check(season: np.ndarray, spread: np.ndarray, margin: np.ndarray, y: np.ndarray) -> dict[str, float]:
    """Held-out RMSE of the second half's relative volume, one season out at a time, for each margin clip."""
    out = {}
    for clip in CLIPS:
        m = margin if clip is None else np.clip(margin, -clip, clip)
        x = np.column_stack([spread, m])
        errors = []
        for held in np.unique(season):
            train = season != held
            b = np.linalg.lstsq(x[train], y[train], rcond=None)[0]
            errors.append((x[~train] @ b - y[~train]) ** 2)
        out[f"clip {clip or 'none'}"] = round(float(np.sqrt(np.mean(np.concatenate(errors)))), 5)
    return out


def spread(games: list[Mapping]) -> dict[str, float]:
    """`plays_sd`: the spread of a team's pace beyond a Poisson's, from its plays before the cut within each
    team-season (Var = mean + mean^2 sd^2); `mix_sd`: the spread of its first-half pass share beyond a binomial's
    (a pass-first or run-first plan for the day)."""
    rows = _rows(games)
    group = rows[:, 1].astype(int)
    plays = sum(_col(rows, part, stat) for part in (0, 1) for stat in ("targets", "carries"))
    mean = float(plays.mean())
    pace = math.sqrt(max(0.0, float(np.var(_within(plays, group))) - mean) / mean ** 2)
    n = _col(rows, 0, "targets") + _col(rows, 0, "carries")
    keep = n > 0
    share = _col(rows, 0, "targets")[keep] / n[keep]
    by_team = np.bincount(group[keep], weights=share) / np.maximum(np.bincount(group[keep]), 1)
    p = by_team[group[keep]]
    excess = float(np.var(share - p)) - float(np.mean(p * (1 - p) / n[keep]))
    return {"plays_sd": round(pace, 4), "mix_sd": round(math.sqrt(max(0.0, excess)), 4),
            "plays_within_sd": round(float(np.std(_within(plays, group))), 2), "plays_poisson_sd": round(math.sqrt(mean), 2)}


def league(games: list[Mapping]) -> dict[str, float]:
    """Mean targets and carries per team-game (a team's window counts are scaled by its volume over these), a
    kneel-down's mean yards, and the quarterback's carries and kneels per team-game (for the docs: the window's
    endings hold the kneels of its last ten minutes, the earlier ones are the halves' share of his carries)."""
    rows = _rows(games)
    total = {name: float(sum(_col(rows, part, name).sum() for part in range(3))) for name in ("kneels", "kneel_yards")}
    return {**{stat: round(float(sum(_col(rows, part, stat).mean() for part in range(3))), 3)
               for stat in ("targets", "carries", "qb_carries", "kneels")},
            "window_kneels": round(float(_col(rows, 2, "kneels").mean()), 3),
            "kneel_yards": round(total["kneel_yards"] / max(total["kneels"], 1.0), 3)}


def touch_rows(session, seasons: Iterable[int]) -> list[tuple[int, str, float, int]]:
    """(season, position, touches the belief expects, real touches) of every healthy player the belief dresses, the
    belief before each of PART_WEEKS against the next WEEKS_AHEAD weeks (0 when he has no box line)."""
    from services import replay_pool, sim_game, sim_world
    out = []
    for season in seasons:
        data = replay_pool.load_season_data(session, season, "half")
        for week in PART_WEEKS:
            world = sim_world.estimate(data.as_of(week), week)
            for team in world.teams:
                side = sim_game._side_of(world, team, None, set())
                for pid, mean in _expected_touches(side).items():
                    for played in range(week, min(week + WEEKS_AHEAD, data.last_week + 1)):
                        tag = data.injuries.get((pid, played))
                        if (team, played) not in data.scores or (tag is not None and tag.hard) \
                                or data.team_at(pid, played + 1) != team:
                            continue
                        line = data.box.get((pid, played))
                        out.append((season, side.positions[pid], mean,
                                    (line.carries + line.targets) if line else 0))
    return out


def _expected_touches(side) -> dict[int, float]:
    from services import sim_game
    m = sim_game._side_means(side)
    touches: dict[int, float] = defaultdict(float)
    for i, pid in enumerate(side.receivers[:-1]):
        touches[pid] += float(m["targets"][i])
    for i, pid in enumerate(side.rushers[:-1]):
        if pid != side.qb:
            touches[pid] += float(m["carries"][i])
    return touches


def qb_share_rows(session, seasons: Iterable[int]) -> list[tuple[int, float, float, float, int]]:
    """(season, the starter's unscaled full-strength share, his real carries, his team's real carries, the carry
    weights dressed) per game he started (50+ passing yards) in the WEEKS_AHEAD weeks after each of PART_WEEKS, his
    share from the belief before it (sim_game._carry_weights, the stored scale taken back out)."""
    from services import replay_pool, sim_game, sim_script, sim_world
    scale = sim_script.qb_share_scale()
    out = []
    for season in seasons:
        data = replay_pool.load_season_data(session, season, "half")
        team_at = {(pid, w): t for pid, games in data.team_by_week.items() for w, t in games}
        for week in PART_WEEKS:
            world = sim_world.estimate(data.as_of(week), week)
            for team in world.teams:
                side = sim_game._side_of(world, team, None, set())
                if side.qb is None:
                    continue
                share = float(side.carry_w[side.rushers.index(side.qb)] / side.carry_w.sum()) / scale
                for played in range(week, min(week + WEEKS_AHEAD, data.last_week + 1)):
                    mine = data.box.get((side.qb, played))
                    if mine is None or mine.pass_yds < 50:
                        continue
                    team_carries = sum(line.carries for (pid, w), line in data.box.items()
                                       if w == played and team_at.get((pid, w)) == team)
                    if team_carries > 0:
                        dressed = sum(world.players[pid].carries for pid in side.rushers[:-1] if pid in world.players)
                        out.append((season, share, mine.carries, team_carries, dressed + world.teams[team].other_carries))
    return out


def qb_share(rows: list[tuple[int, float, float, float, int]]) -> dict:
    """The league-wide factor on a QB's full-strength carry share: his real carries over his unscaled share x his
    team's real carries, summed over the rows. Every dressed player counts in the share, though real games see only
    some of them, so it runs short; the same factor in the belief and the truth, week 1 to the last. `check`: held
    out one season at a time, the squared error of his carries against the unscaled share's; `groups`: the factor
    for small / middle / large shares (a pocket passer to a runner) and for light / heavy dressed carry weights (the
    sum the share divides by), to see whether one factor fits them all."""
    a = np.array(rows, dtype=float)
    season, share, qb, team, dressed = a.T

    def factor(k: np.ndarray) -> float:
        return float(qb[k].sum() / (share[k] * team[k]).sum()) if k.any() else float("nan")
    held = without = 0.0
    for s in np.unique(season):
        f = factor(season != s)
        test = season == s
        held += float(np.sum((qb[test] - f * share[test] * team[test]) ** 2))
        without += float(np.sum((qb[test] - share[test] * team[test]) ** 2))
    edges = np.quantile(share, [1 / 3, 2 / 3])
    groups = {"share small": factor(share < edges[0]), "share middle": factor((share >= edges[0]) & (share < edges[1])),
              "share large": factor(share >= edges[1]), "dressed light": factor(dressed <= np.median(dressed)),
              "dressed heavy": factor(dressed > np.median(dressed))}
    return {"scale": round(factor(np.ones(len(a), dtype=bool)), 4), "games": len(a),
            "check": {"held_out_sse": round(held / len(a), 3), "unscaled_sse": round(without / len(a), 3)},
            "groups": {k: round(v, 4) for k, v in groups.items()}}


def zero_chance(mu: np.ndarray, c: float, scale: float, floor: float) -> np.ndarray:
    """The chance a player expected `mu` touches has none: a no-show, else out of the game plan, else none of a
    Poisson around mu / q."""
    q = np.clip(1.0 - c * np.exp(-mu / scale), 1e-3, 1.0)
    return floor + (1.0 - floor) * ((1.0 - q) + q * np.exp(-mu / q))


def _loss(rows: np.ndarray, c: float, scale: float, floor: float) -> float:
    p = np.clip(zero_chance(rows[:, 1], c, scale, floor), 1e-6, 1 - 1e-6)
    return float(-np.sum(np.where(rows[:, 2] == 0, np.log(p), np.log(1 - p))))


def _best(rows: np.ndarray, floor: float) -> tuple[float, float]:
    return min(((float(c), float(s)) for c in GRID_C for s in GRID_SCALE), key=lambda g: _loss(rows, *g, floor))


def participation(rows: list[tuple[int, str, float, int]]) -> dict:
    """{position: the participation curve and its held-out check} (see the module docstring); a position with no
    rows has none (sim_script.in_plan keeps its players always in)."""
    pooled = np.array([(r[0], r[2], r[3]) for r in rows], dtype=float)
    out = {}
    for position in ("RB", "WR", "TE"):
        mine = [(r[0], r[2], r[3]) for r in rows if r[1] == position]
        if mine:
            out[position] = _curve(np.array(mine, dtype=float), pooled)
    return out


def _floor(a: np.ndarray, pooled: np.ndarray, seasons: np.ndarray) -> float:
    """The no-show floor from `seasons` only: the zero share of the position's players expected FLOOR_TOUCHES or
    more, or every position's (`pooled`) with fewer than FLOOR_ROWS of them."""
    big = a[(a[:, 1] >= FLOOR_TOUCHES) & np.isin(a[:, 0], seasons)]
    if len(big) < FLOOR_ROWS:
        big = pooled[(pooled[:, 1] >= FLOOR_TOUCHES) & np.isin(pooled[:, 0], seasons)]
    return float(np.mean(big[:, 2] == 0)) if len(big) else 0.0


def _curve(a: np.ndarray, pooled: np.ndarray) -> dict:
    """One position's curve from rows of (season, expected touches, real touches); held out one season at a time,
    the floor too worked out from the other seasons only."""
    seasons = np.unique(a[:, 0])
    floor = _floor(a, pooled, seasons)
    c, scale = _best(a, floor)
    held = without = 0.0
    for season in seasons:
        train, test = a[a[:, 0] != season], a[a[:, 0] == season]
        fold = _floor(a, pooled, seasons[seasons != season])
        held += _loss(test, *_best(train, fold), fold)
        without += _loss(test, 0.0, 1.0, fold)
    bands = []
    for low, high in ((0, 1), (1, 2), (2, 3), (3, 4), (4, 6), (6, 8), (8, 99)):
        k = (a[:, 1] >= low) & (a[:, 1] < high)
        if k.any():
            bands.append({"touches": [low, high], "players": int(k.sum()), "real_zero": round(float(np.mean(a[k, 2] == 0)), 3),
                          "model_zero": round(float(np.mean(zero_chance(a[k, 1], c, scale, floor))), 3)})
    return {"c": round(c, 3), "scale": round(scale, 3), "floor": round(floor, 4),
            "check": {"held_out_loss": round(held / len(a), 4), "without": round(without / len(a), 4)}, "bands": bands}


def fit(seasons: Iterable[int], refresh: bool = False) -> dict:
    from database.db import SessionLocal
    seasons = sorted(set(seasons))
    games = segments(seasons, refresh)
    with SessionLocal() as session:
        touch = touch_rows(session, [s for s in seasons if s >= 2019])     # projections and ADP from 2019 on
        qb = qb_share_rows(session, [s for s in seasons if s >= 2019])
        session.rollback()
    return {"seasons": [seasons[0], seasons[-1]], "games": len(games), "cut": CUT, **halves(games),
            "spread": spread(games), "league": league(games), "qb_share": qb_share(qb), "participation": participation(touch)}


def write(result: Mapping) -> None:
    atomic_write(SCRIPT_PATH, json.dumps(result, indent=1) + "\n")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fit", action="store_true", help="measure and write sim_script.json")
    parser.add_argument("--seasons", default=f"{SEASONS.start}-{SEASONS.stop - 1}")
    parser.add_argument("--refresh", action="store_true", help="download the play-by-play again")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    first, _, last = args.seasons.partition("-")
    result = fit(range(int(first), int(last or first) + 1), args.refresh)
    if args.fit:
        write(result)
        log.info("wrote %s", SCRIPT_PATH.name)
    for key in ("shares", "first_half", "second_half", "check", "spread", "league", "qb_share"):
        log.info("%-12s %s", key, result[key])
    for position, curve in result["participation"].items():
        log.info("participation %s %s", position, {k: v for k, v in curve.items() if k != "bands"})
        for band in curve["bands"]:
            log.info("  %s", band)


if __name__ == "__main__":
    main()
