"""Simulated seasons: fit the closing minutes and overtime of a game from real play-by-play (writes sim_closing.json).

    python -m services.sim_closing_fit --fit [--seasons 2018-2025] [--window 300] [--refresh]

Real margins land on exactly 3 or 7 far more often than two teams scoring independently would make them (2018-2025:
3 in 14.4% of games, pairing each team's score with another game's opponent gives 6.1%), because play late in a game
follows the score: a tied or close team kicks to win, a team down 4-8 chases a touchdown, a leader runs the clock.
sim_closing plays the last WINDOW seconds of regulation from the score with what real teams did from that score:

- `table`: for each margin at the cut (0-MAX_MARGIN, the trailing team's deficit; larger ones pooled), every real
  game's scoring events after it, the trailer's and the leader's (for a tie: the two teams in random order), as
  (count, trailer events, leader events) with the events in EVENTS order. A drawn row is a real game's ending.
- `per_side`: the mean events a team scores in the window (what the regulation draw leaves out: sim_centre).
- `ot`: overtime from the same play-by-play: the share of overtimes that end tied, the share of decided ones the
  winner ends with a field goal, and the mean yards of an overtime-winning TD play by a catch and by a run.
- `check`: leave-season-out, each season's real cut scores played forward with the other seasons' table, against
  that season's real end of regulation (shares of margins 1-3, 3, 7, 4-8 and ties).
- `line_script`: how a team's volume follows its pregame expected margin (the Vegas spread), within each
  team-season so a team's own style is not counted: log targets and log carries per point of expected margin
  (2018-2025: favourites run about 0.9% more a point and throw about 0.1% less). Read from the database
  (player_stats_weekly and nfl_games), applied by sim_centre before a game is centred.

The scoring plays are read once with nflreadpy (the play-by-play the stats load already uses) and cached in
PLAYS_CACHE (git-ignored); the network is needed the first time, with --refresh, or for a season not in it.
"""
import argparse
import json
import logging
import os
import random
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path

import numpy as np

from integrations.weekly_sources import TEAM_ALIASES

log = logging.getLogger(__name__)

CLOSING_PATH = Path(__file__).with_name("sim_closing.json")
PLAYS_CACHE = Path(__file__).resolve().parent.parent / "data" / "cache" / "scoring_plays.json"
EVENTS = ("pass_td", "rush_td", "ret_td", "fg_0_39", "fg_40_49", "fg_50", "miss_0_39", "miss_40_49", "miss_50",
          "xp", "xp_miss", "two", "safety")
POINTS = {"pass_td": 6, "rush_td": 6, "ret_td": 6, "fg_0_39": 3, "fg_40_49": 3, "fg_50": 3, "xp": 1, "two": 2,
          "safety": 2}
WINDOW = 600                     # seconds of regulation played from the score (chosen with sim_eval: see sim_closing)
MAX_MARGIN = 16                  # margins above this share one row set (a two-score-plus lead plays alike)
SEASONS = range(2018, 2026)
SHUFFLE_SEED = 7                 # the random order of a tied game's two teams (reproducible fits)


# ---------------- the scoring plays (cached) ----------------

def _band(distance: float | None) -> str:
    d = distance or 0
    return "0_39" if d < 40 else "40_49" if d < 50 else "50"


def _kind(r: Mapping) -> tuple[str, str] | None:
    """(team, event) of one play, or None when it scores nothing the box score keeps."""
    if r["touchdown"] == 1 and r["td_team"]:
        kind = "pass_td" if r["pass_touchdown"] == 1 else "rush_td" if r["rush_touchdown"] == 1 else "ret_td"
        return r["td_team"], kind
    if r["field_goal_result"] in ("made", "missed", "blocked"):
        return r["posteam"], ("fg_" if r["field_goal_result"] == "made" else "miss_") + _band(r["kick_distance"])
    if r["extra_point_result"] in ("good", "failed", "blocked"):
        return r["posteam"], "xp" if r["extra_point_result"] == "good" else "xp_miss"
    if r["two_point_conv_result"] == "success":
        return r["posteam"], "two"
    if r["safety"] == 1:
        return r["defteam"], "safety"
    return None


def _download(season: int) -> list[dict]:
    """One season's regular-season games: teams, final scores and every scoring play (quarter, seconds left,
    team, event, yards gained)."""
    import nflreadpy as nfl
    import polars as pl

    cols = ["game_id", "week", "home_team", "away_team", "home_score", "away_score", "qtr", "game_seconds_remaining",
            "posteam", "defteam", "touchdown", "td_team", "pass_touchdown", "rush_touchdown", "field_goal_result",
            "kick_distance", "extra_point_result", "two_point_conv_result", "safety", "yards_gained"]
    pbp = nfl.load_pbp(season).filter(pl.col("season_type") == "REG").select(cols)
    games: dict[str, dict] = {}
    for r in pbp.iter_rows(named=True):
        g = games.setdefault(r["game_id"], {"week": r["week"], "home": TEAM_ALIASES.get(r["home_team"], r["home_team"]),
                                            "away": TEAM_ALIASES.get(r["away_team"], r["away_team"]),
                                            "home_score": r["home_score"], "away_score": r["away_score"], "plays": []})
        kind = _kind(r)
        if kind is not None and r["game_seconds_remaining"] is not None:
            team = TEAM_ALIASES.get(kind[0], kind[0])
            g["plays"].append([int(r["qtr"]), int(r["game_seconds_remaining"]), team, kind[1],
                               int(r["yards_gained"] or 0)])
    return [{"season": season, **g} for g in games.values()]


def scoring_games(seasons: Iterable[int], refresh: bool = False) -> list[dict]:
    """Every real game of `seasons` with its scoring plays, from PLAYS_CACHE (downloaded when missing)."""
    wanted = sorted(set(seasons))
    cached = {} if refresh else _cached()
    missing = [s for s in wanted if str(s) not in cached]
    if missing:
        for season in missing:
            log.info("downloading %s play-by-play", season)
            cached[str(season)] = _download(season)
        PLAYS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        partial = PLAYS_CACHE.with_suffix(f".{os.getpid()}.tmp")
        partial.write_text(json.dumps(cached), encoding="utf-8")
        os.replace(partial, PLAYS_CACHE)
    return [g for s in wanted for g in cached[str(s)]]


def _cached() -> dict:
    try:
        cached = json.loads(PLAYS_CACHE.read_text(encoding="utf-8"))
        if isinstance(cached, dict) and all(isinstance(v, list) for v in cached.values()):
            return cached
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        pass
    log.info("%s is unreadable: downloading the play-by-play again", PLAYS_CACHE.name)
    return {}


# ---------------- the fit ----------------

def _points(events: Mapping[str, int]) -> int:
    return sum(POINTS.get(k, 0) * n for k, n in events.items())


def split(game: Mapping, window: int) -> tuple[dict[str, Counter], dict[str, Counter], dict[str, Counter]]:
    """(events before the cut, in the window, in overtime) per team of one game."""
    parts: tuple[dict, dict, dict] = ({}, {}, {})
    for team in (game["home"], game["away"]):
        for part in parts:
            part[team] = Counter()
    for qtr, left, team, kind, _ in game["plays"]:
        if team in parts[0]:
            part = parts[2] if qtr > 4 else parts[1] if qtr == 4 and left <= window else parts[0]
            part[team][kind] += 1
    return parts


def consistent(game: Mapping) -> bool:
    """Whether the scoring plays add up to the final score (a few games carry a play nflverse misses)."""
    before, inside, overtime = split(game, 0)
    return all(_points(before[t]) + _points(inside[t]) + _points(overtime[t]) == score
               for t, score in ((game["home"], game["home_score"]), (game["away"], game["away_score"])))


def _row(c: Counter) -> tuple[int, ...]:
    return tuple(int(c.get(k, 0)) for k in EVENTS)


def endings(games: Iterable[Mapping], window: int, seed: int = SHUFFLE_SEED) -> dict[int, Counter]:
    """{margin at the cut (capped at MAX_MARGIN + 1): Counter((trailer events, leader events))}."""
    rnd = random.Random(seed)
    table: dict[int, Counter] = defaultdict(Counter)
    for g in games:
        before, inside, _ = split(g, window)
        home, away = g["home"], g["away"]
        margin = _points(before[home]) - _points(before[away])
        trail, lead = (home, away) if margin < 0 else (away, home)
        if margin == 0 and rnd.random() < 0.5:
            trail, lead = lead, trail
        table[min(abs(margin), MAX_MARGIN + 1)][(_row(inside[trail]), _row(inside[lead]))] += 1
    return table


def td_yards(games: Iterable[Mapping], window: int) -> dict[str, float]:
    """Mean yards of a window TD play, by a catch and by a run (sim_closing credits them to the scorer)."""
    yards: dict[str, list[int]] = {"rec": [], "rush": []}
    for g in games:
        for qtr, left, _, kind, gained in g["plays"]:
            if qtr == 4 and left <= window and kind in ("pass_td", "rush_td"):
                yards["rec" if kind == "pass_td" else "rush"].append(gained)
    return {k: round(float(np.mean(v)), 1) if v else 0.0 for k, v in yards.items()}


def overtime(games: Iterable[Mapping]) -> dict:
    """Overtime as real games played it (see the module docstring)."""
    tied, field_goal, decided = 0, 0, 0
    yards: dict[str, list[int]] = {"rec": [], "rush": []}
    for g in games:
        if not any(p[0] > 4 for p in g["plays"]):
            continue
        if g["home_score"] == g["away_score"]:
            tied += 1
            continue
        decided += 1
        winner = g["home"] if g["home_score"] > g["away_score"] else g["away"]
        last = [p for p in g["plays"] if p[0] > 4 and p[2] == winner and p[3] not in ("xp", "xp_miss", "two")]
        if last and last[-1][3].startswith("fg_"):
            field_goal += 1
        elif last and last[-1][3] in ("pass_td", "rush_td"):
            yards["rec" if last[-1][3] == "pass_td" else "rush"].append(last[-1][4])
    return {"games": tied + decided, "tie": tied / max(1, tied + decided), "field_goal": field_goal / max(1, decided),
            "td_yards": {k: round(float(np.mean(v)), 1) if v else 0.0 for k, v in yards.items()}}


def play_forward(cut: tuple[int, int], table: Mapping[int, Counter], rng: random.Random) -> int:
    """The end-of-regulation margin (home minus away) of a game at `cut` (home, away points) played with `table`."""
    home, away = cut
    margin = abs(home - away)
    rows = table[min(margin, MAX_MARGIN + 1)]
    trail_row, lead_row = rng.choices(list(rows), weights=list(rows.values()))[0]
    gained = _points(dict(zip(EVENTS, lead_row))) - _points(dict(zip(EVENTS, trail_row)))
    if margin == 0:
        return gained if rng.random() < 0.5 else -gained
    return (margin + gained) * (1 if home > away else -1)


def shares(margins: Iterable[int]) -> dict[str, float]:
    m = np.abs(np.asarray(list(margins)))
    return {"1-3": float(np.mean((m >= 1) & (m <= 3))), "3": float(np.mean(m == 3)), "7": float(np.mean(m == 7)),
            "4-8": float(np.mean((m >= 4) & (m <= 8))), "tie": float(np.mean(m == 0))}


def check(games: list[Mapping], window: int, repeats: int = 20) -> dict[str, dict[str, float]]:
    """Leave-season-out: each season's real cut scores played forward with the other seasons' table."""
    rng = random.Random(SHUFFLE_SEED)
    real, played = [], []
    for season in sorted({g["season"] for g in games}):
        table = endings([g for g in games if g["season"] != season], window)
        for g in (g for g in games if g["season"] == season):
            before, inside, _ = split(g, window)
            home, away = _points(before[g["home"]]), _points(before[g["away"]])
            real.append(home + _points(inside[g["home"]]) - away - _points(inside[g["away"]]))
            played.extend(play_forward((home, away), table, rng) for _ in range(repeats))
    return {"real": shares(real), "played forward": shares(played)}


def line_script(session, seasons: Iterable[int]) -> dict[str, float]:
    """{"targets", "carries"}: the within-team-season slope of a team-game's log volume on its Vegas expected margin
    (see the module docstring)."""
    from sqlalchemy import text

    wanted = sorted(set(seasons))
    spreads = {}
    for season, week, home, away, spread in session.execute(text(
            "select season, week, home_team, away_team, spread_line from nfl_games where game_type = 'REG' "
            "and spread_line is not null")):
        if season in wanted:
            spreads[(season, week, TEAM_ALIASES.get(home, home))] = (TEAM_ALIASES.get(away, away), float(spread))
            spreads[(season, week, TEAM_ALIASES.get(away, away))] = (TEAM_ALIASES.get(home, home), -float(spread))
    by_team: dict[tuple[int, str], list[tuple[float, float, float]]] = defaultdict(list)
    for season, week, opponent, targets, carries in session.execute(text(
            "select season, week, opponent_team, sum(coalesce(targets, 0)), sum(coalesce(carries, 0)) "
            "from player_stats_weekly where season_type = 'REG' group by season, week, opponent_team")):
        opponent = TEAM_ALIASES.get(opponent, opponent)
        found = spreads.get((season, week, opponent))
        if found is None or not targets or not carries:
            continue
        team, margin = found[0], -found[1]                 # the team facing `opponent`: its expected margin
        by_team[(season, team)].append((margin, np.log(targets), np.log(carries)))
    xs, ts, cs = [], [], []
    for rows in by_team.values():
        if len(rows) >= 4:
            a = np.array(rows) - np.mean(rows, axis=0)
            xs.append(a[:, 0])
            ts.append(a[:, 1])
            cs.append(a[:, 2])
    x, t, c = (np.concatenate(v) for v in (xs, ts, cs))
    return {"targets": round(float(x @ t / (x @ x)), 5), "carries": round(float(x @ c / (x @ x)), 5),
            "team_games": int(len(x))}


def fit(seasons: Iterable[int], window: int = WINDOW, refresh: bool = False) -> dict:
    from database.db import SessionLocal

    with SessionLocal() as session:
        script = line_script(session, seasons)
    games = [g for g in scoring_games(seasons, refresh) if consistent(g)]
    table = endings(games, window)
    sides = [_row(inside[t]) for g in games for inside in (split(g, window)[1],) for t in (g["home"], g["away"])]
    return {"seasons": [min(g["season"] for g in games), max(g["season"] for g in games)], "games": len(games),
            "window": window, "events": list(EVENTS), "max_margin": MAX_MARGIN,
            "td_yards": td_yards(games, window),
            "per_side": dict(zip(EVENTS, (round(float(v), 5) for v in np.mean(sides, axis=0)))),
            "table": {str(k): sorted([[n, list(t), list(lead)] for (t, lead), n in rows.items()], reverse=True)
                      for k, rows in sorted(table.items())},
            "ot": overtime(games), "line_script": script, "check": check(games, window)}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fit", action="store_true", help="measure and write sim_closing.json")
    parser.add_argument("--seasons", default=f"{SEASONS.start}-{SEASONS.stop - 1}")
    parser.add_argument("--window", type=int, default=WINDOW)
    parser.add_argument("--refresh", action="store_true", help="download the play-by-play again")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    first, _, last = args.seasons.partition("-")
    result = fit(range(int(first), int(last or first) + 1), args.window, args.refresh)
    log.info("%d games, window %ds; per side %s", result["games"], result["window"], result["per_side"])
    log.info("overtime %s", result["ot"])
    log.info("volume per point of expected margin %s", result["line_script"])
    for name, row in result["check"].items():
        log.info("%-15s %s", name, "  ".join(f"{k} {100 * v:.1f}%" for k, v in row.items()))
    if args.fit:
        CLOSING_PATH.write_text(json.dumps(result, separators=(",", ":")) + "\n", encoding="utf-8")
        log.info("wrote %s", CLOSING_PATH.name)


if __name__ == "__main__":
    main()
