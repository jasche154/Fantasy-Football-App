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
  at half time (a team down two scores after the half throws about 20% more and runs about 13% less). Linear in
  the margin: clipping it at 10, 14 or 21 points does no better on held-out seasons (`check`).
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
  share spreads 0.109 against the binomial's 0.092: a pass-first or run-first plan, sd 0.06).
- `league`: mean targets and carries per team-game of the same games (the window's real counts are scaled by a
  team's own volume over these), the mean yards of a kneel-down.
"""
import argparse
import json
import math
import logging
import os
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path

import numpy as np

from integrations.weekly_sources import TEAM_ALIASES

log = logging.getLogger(__name__)

SCRIPT_PATH = Path(__file__).with_name("sim_script.json")
SEGMENTS_CACHE = Path(__file__).resolve().parent.parent / "data" / "cache" / "script_segments.json"
SEASONS = range(2018, 2026)
CUT = 600                        # seconds of regulation in the closing window (sim_closing_fit.WINDOW)
COUNTS = ("margin", "targets", "attempts", "sacks", "carries", "qb_carries", "qb_yards", "kneels", "kneel_yards",
          "scrambles", "spikes")
PART_WEEKS = (6, 10, 14)         # the belief before each of these weeks is judged on the next WEEKS_AHEAD weeks
WEEKS_AHEAD = 4
FLOOR_TOUCHES = 8.0              # players expected this many touches set the no-show floor
FLOOR_ROWS = 200                 # ... when a position has this many player-games of them (tight ends: 45)
CLIPS = (None, 21, 14, 10)       # second-half margin clips compared on held-out seasons
GRID_C = np.arange(0.30, 1.0001, 0.02)
GRID_SCALE = np.arange(0.5, 12.0, 0.25)


# ---------------- the segment counts (cached) ----------------

def _download(season: int) -> list[dict]:
    """One season's regular-season games with each team's counts per part (see the module docstring)."""
    import nflreadpy as nfl
    import polars as pl

    cols = ["game_id", "week", "home_team", "away_team", "spread_line", "qtr", "game_seconds_remaining", "play_id",
            "posteam", "defteam", "posteam_score", "defteam_score", "play_type", "pass_attempt", "rush_attempt", "sack",
            "receiver_player_id", "two_point_attempt", "qb_kneel", "qb_spike", "qb_scramble", "rusher_player_id",
            "passer_player_id", "yards_gained"]
    pbp = nfl.load_pbp(season).filter(pl.col("season_type") == "REG").select(cols).sort(["game_id", "play_id"])
    return [g for _, rows in pbp.group_by("game_id", maintain_order=True)
            if (g := game_counts(list(rows.iter_rows(named=True)), season)) is not None]


def _passers(rows: list[Mapping]) -> dict[str, str | None]:
    """{team: the id of the player who threw the most passes} (his carries are the quarterback's)."""
    thrown: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        if r["passer_player_id"] is not None and r["posteam"]:
            thrown[r["posteam"]][r["passer_player_id"]] += 1
    return {team: max(counts, key=counts.get) for team, counts in thrown.items()}


def game_counts(rows: list[Mapping], season: int) -> dict | None:
    """One game's {"season", "week", "home", "away", "spread", home team: [part counts x3], away team: ...} from its
    play-by-play rows in play order; None when a part has no play with a score (a broken feed) or no spread."""
    first = rows[0]
    home, away, spread = first["home_team"], first["away_team"], first["spread_line"]
    if spread is None:
        return None
    qb = _passers(rows)
    counts = {(t, part): dict.fromkeys(COUNTS, 0) for t in (home, away) for part in range(3)}
    entering: dict[int, dict[str, int]] = {}
    for r in rows:
        qtr, left, team = r["qtr"], r["game_seconds_remaining"], r["posteam"]
        if qtr is None or qtr > 4 or left is None or team not in (home, away):
            continue
        part = 0 if qtr <= 2 else 1 if left > CUT else 2
        if part not in entering and r["posteam_score"] is not None:
            entering[part] = {team: r["posteam_score"], r["defteam"]: r["defteam_score"]}
        if r["two_point_attempt"] == 1 or r["play_type"] in (None, "no_play"):
            continue
        c = counts[(team, part)]
        if r["rush_attempt"] == 1:
            c["carries"] += 1
            mine = r["qb_kneel"] == 1 or (r["rusher_player_id"] is not None and r["rusher_player_id"] == qb.get(team))
            c["qb_carries"] += int(mine)
            c["qb_yards"] += int(r["yards_gained"] or 0) if mine else 0
            c["kneels"] += int(r["qb_kneel"] == 1)
            c["kneel_yards"] += int(r["yards_gained"] or 0) if r["qb_kneel"] == 1 else 0
            c["scrambles"] += int(r["qb_scramble"] == 1)
        if r["pass_attempt"] == 1:
            if r["sack"] == 1:
                c["sacks"] += 1
            else:
                c["attempts"] += 1
                c["spikes"] += int(r["qb_spike"] == 1)
                c["targets"] += int(r["receiver_player_id"] is not None)
    if len(entering) < 3:
        return None
    out = {"season": season, "week": first["week"], "home": TEAM_ALIASES.get(home, home),
           "away": TEAM_ALIASES.get(away, away), "spread": float(spread)}
    for team, other in ((home, away), (away, home)):
        parts = []
        for part in range(3):
            c = {**counts[(team, part)], "margin": entering[part].get(team, 0) - entering[part].get(other, 0)}
            parts.append([c[k] for k in COUNTS])
        out[TEAM_ALIASES.get(team, team)] = parts
    return out


def segments(seasons: Iterable[int], refresh: bool = False) -> list[dict]:
    """Every real game of `seasons` with its parts' counts, from SEGMENTS_CACHE (downloaded when missing, written in
    another column layout, or with `refresh`; the other seasons in the cache stay)."""
    wanted = sorted(set(seasons))
    cached = _cached()
    missing = [s for s in wanted if refresh or str(s) not in cached]
    if missing:
        for season in missing:
            log.info("downloading %s play-by-play", season)
            cached[str(season)] = _download(season)
        SEGMENTS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(SEGMENTS_CACHE, json.dumps(cached))
    return [g for s in wanted for g in cached[str(s)]]


def atomic_write(path: Path, text: str) -> None:
    """`text` to `path` through a temporary file, which a failed write never leaves behind."""
    partial = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        partial.write_text(text, encoding="utf-8")
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)


def _cached() -> dict:
    """SEGMENTS_CACHE's seasons whose every game has len(COUNTS) counts per part (a season cached in another
    column layout is left out, so it is downloaded again: reading it by position would mix the columns up)."""
    try:
        cached = json.loads(SEGMENTS_CACHE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log.warning("%s is unreadable: downloading the play-by-play again", SEGMENTS_CACHE.name, exc_info=True)
        return {}
    if not (isinstance(cached, dict) and all(isinstance(v, list) for v in cached.values())):
        log.warning("%s is not a {season: games} file: downloading the play-by-play again", SEGMENTS_CACHE.name)
        return {}
    kept = {season: games for season, games in cached.items() if all(_laid_out(g) for g in games)}
    if len(kept) < len(cached):
        log.warning("%s: seasons %s were cached in another column layout: downloading them again",
                    SEGMENTS_CACHE.name, sorted(set(cached) - set(kept)))
    return kept


def _laid_out(game: Mapping) -> bool:
    teams = [game.get("home"), game.get("away")]
    return all(isinstance(game.get(t), list) and len(game[t]) == 3 and all(len(part) == len(COUNTS) for part in game[t])
               for t in teams)


def window_counts(game: Mapping, team: str) -> dict[str, int]:
    """A team's closing-window targets, carries and kneels in one cached game (sim_closing_fit's ending table)."""
    part = dict(zip(COUNTS, game[team][2]))
    return {"targets": int(part["targets"]), "carries": int(part["carries"]), "kneels": int(part["kneels"])}


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
        session.rollback()
    return {"seasons": [seasons[0], seasons[-1]], "games": len(games), "cut": CUT, **halves(games),
            "spread": spread(games), "league": league(games), "participation": participation(touch)}


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
    for key in ("shares", "first_half", "second_half", "check", "spread", "league"):
        log.info("%-12s %s", key, result[key])
    for position, curve in result["participation"].items():
        log.info("participation %s %s", position, {k: v for k, v in curve.items() if k != "bands"})
        for band in curve["bands"]:
            log.info("  %s", band)


if __name__ == "__main__":
    main()
