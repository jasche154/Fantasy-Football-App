"""Simulated seasons: fit the closing minutes and overtime of a game from real play-by-play (writes sim_closing.json).

    python -m services.sim_closing_fit --fit [--seasons 2018-2025] [--refresh]

The window is WINDOW (600 s, chosen with sim_eval), the cut sim_script_fit counts each part's plays at (its CUT).

Real margins land on exactly 3 or 7 far more often than two teams scoring independently would make them (2018-2025:
3 in 14.4% of games, pairing each team's score with another game's opponent gives 6.1%), because play late in a game
follows the score: a tied or close team kicks to win, a team down 4-8 chases a touchdown, a leader runs the clock.
sim_closing plays the last WINDOW seconds of regulation from the score with what real teams did from that score:

- `table`: for each margin at the cut (0-MAX_MARGIN, the trailing team's deficit; larger ones pooled), every real
  game's scoring events after it, the trailer's and the leader's (for a tie: the two teams in random order) and the
  trailer's Vegas spread (sim_closing draws an ending near the game's own expected margin: real favourites gain
  0.21 points a point of the spread in the window at the same cut margin), with
  its plays there (targets, carries and kneel-downs, PLAYS, from sim_script_fit's cache: run that fit first), as
  (count, trailer events, leader events) with the events in EVENTS order. A drawn row is a real game's ending: the
  leader runs and kneels, the trailer throws (sim_script).
- `ot`: overtime from the same play-by-play: the share of overtimes that end tied, the share of decided ones the
  winner ends with a field goal, and the mean yards of an overtime-winning TD play by a catch and by a run.
- `cut_var`: the spread of a simulated game's margin at the cut, a x (pa ^ b + pb ^ b) of its two sides' expected
  points before the cut
  (`cut_spread`, measured on the simulator's own draws, CUT_SEASON's belief; re-measure when the draw changes);
- `check`: leave-season-out, each season's real cut scores played forward with the other seasons' table, against
  that season's real end of regulation (shares of margins 1-3, 3, 7, 4-8 and ties).
- `line_script`: how a team's volume follows its pregame expected margin (the Vegas spread), within each
  team-season so a team's own style is not counted: log targets and log carries per point of expected margin
  (2018-2025: carries +0.76% a point of the favourite's expected margin, targets -0.03%). Read from the database
  (player_stats_weekly and nfl_games); a real reference for sim_eval's "per line point" rows. The simulated game
  follows the line through its parts (sim_script: the halves' fitted slopes, the second half's response to the
  lead and the window's real endings), not through this number.

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
from services.sim_closing_plays import (  # noqa: F401  (re-exported: the fit's import path)
    PLAYS_CACHE, _band, _cached, _download, _kind, games_of, scoring_games,
)

log = logging.getLogger(__name__)

CLOSING_PATH = Path(__file__).with_name("sim_closing.json")
EVENTS = ("pass_td", "rush_td", "ret_td", "fg_0_39", "fg_40_49", "fg_50", "miss_0_39", "miss_40_49", "miss_50",
          "xp", "xp_miss", "two", "safety", "targets", "carries", "kneels")
PLAYS = ("targets", "carries", "kneels")       # the window's plays (sim_script_fit's cache), its scoring ones among them
POINTS = {"pass_td": 6, "rush_td": 6, "ret_td": 6, "fg_0_39": 3, "fg_40_49": 3, "fg_50": 3, "xp": 1, "two": 2,
          "safety": 2}
WINDOW = 600                     # seconds of regulation played from the score (chosen with sim_eval: see sim_closing)
TRIES = ("xp", "xp_miss", "two")
CUT_SEASON, CUT_WEEK, CUT_GAMES = 2024, 9, 3          # the belief the cut margin's spread is measured on
CUT_TARGETS = ((10, 13), (14, 10), (17, 17), (24, 20), (28, 28), (31, 13), (38, 10), (21, 21), (35, 30))
CUT_DRAWS = 1500
CUT_VAR_START = (12.0, 0.6)      # stored until `write` measures it, when the file has none
MAX_MARGIN = 16                  # margins above this share one row set (a two-score-plus lead plays alike)
SEASONS = range(2018, 2026)
SHUFFLE_SEED = 7                 # the random order of a tied game's two teams (reproducible fits)


# ---------------- the fit ----------------

def _points(events: Mapping[str, int]) -> int:
    return sum(POINTS.get(k, 0) * n for k, n in events.items())


def split(game: Mapping, window: int) -> tuple[dict[str, Counter], dict[str, Counter], dict[str, Counter]]:
    """(events before the cut, in the window, in overtime) per team of one game. A try (extra point or two-point
    conversion) is filed with the part its touchdown is in, so a touchdown just before the cut never leaves its
    try alone in the window (14 of 4,230 side-endings had more tries than touchdowns)."""
    parts: tuple[dict, dict, dict] = ({}, {}, {})
    for team in (game["home"], game["away"]):
        for part in parts:
            part[team] = Counter()
    last_td: dict[str, int] = {}                           # each team's last touchdown's part
    for qtr, left, team, kind, _ in game["plays"]:
        if team in parts[0]:
            index = 2 if qtr > 4 else 1 if qtr == 4 and left <= window else 0
            if kind in TRIES and team in last_td:
                index = last_td[team]
            elif kind.endswith("_td"):
                last_td[team] = index
            parts[index][team][kind] += 1
    return parts


def consistent(game: Mapping) -> bool:
    """Whether the scoring plays add up to the final score (a few games carry a play nflverse misses)."""
    before, inside, overtime = split(game, 0)
    return all(_points(before[t]) + _points(inside[t]) + _points(overtime[t]) == score
               for t, score in ((game["home"], game["home_score"]), (game["away"], game["away_score"])))


def _row(c: Counter) -> tuple[int, ...]:
    return tuple(int(c.get(k, 0)) for k in EVENTS)


def endings(games: Iterable[Mapping], window: int, seed: int = SHUFFLE_SEED) -> dict[int, Counter]:
    """{margin at the cut (capped at MAX_MARGIN + 1): Counter((trailer events, leader events, the trailer's Vegas
    spread))}; a game's window plays (`plays_in_window`: {team: {PLAYS name: count}}) ride on its events when it has
    them, its spread (`spread`, the home side's) when it has one (0.0 else)."""
    rnd = random.Random(seed)
    table: dict[int, Counter] = defaultdict(Counter)
    for g in games:
        before, inside, _ = split(g, window)
        for team, counts in g.get("plays_in_window", {}).items():
            inside[team].update(counts)
        home, away = g["home"], g["away"]
        margin = _points(before[home]) - _points(before[away])
        trail, lead = (home, away) if margin < 0 else (away, home)
        if margin == 0 and rnd.random() < 0.5:
            trail, lead = lead, trail
        spread = float(g.get("spread", 0.0)) * (1.0 if trail == home else -1.0)      # the trailer's Vegas edge
        table[min(abs(margin), MAX_MARGIN + 1)][(_row(inside[trail]), _row(inside[lead]), round(spread * 2) / 2)] += 1
    return table


def with_plays(games: list[Mapping], window: int, seasons: Iterable[int], refresh: bool = False) -> list[dict]:
    """`games` that sim_script_fit's cache has, each with its teams' window plays (`plays_in_window`); that cache is
    sim_script_fit's to refresh (`refresh` is for the scoring plays: one download each)."""
    from services import sim_script_fit
    if window != sim_script_fit.CUT:
        raise ValueError(f"the window plays are counted for a {sim_script_fit.CUT}s window, not {window}s")
    cached = {(g["season"], g["week"], g["home"], g["away"]): g for g in sim_script_fit.segments(seasons)}
    out = []
    for g in games:
        found = cached.get((g["season"], g["week"], g["home"], g["away"]))
        if found is not None:
            out.append({**g, "spread": found["spread"],
                        "plays_in_window": {t: sim_script_fit.window_counts(found, t) for t in (g["home"], g["away"])}})
    return out


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
        # `plays` holds scoring plays and kicks only: an overtime with neither, ended tied, shows only in the score
        if g["home_score"] != g["away_score"] and not any(p[0] > 4 for p in g["plays"]):
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
    trail_row, lead_row = rng.choices(list(rows), weights=list(rows.values()))[0][:2]
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


def cut_spread(world, games: Iterable[tuple[str, str]], draws: int = CUT_DRAWS, seed: int = 0) -> list[float]:
    """[a, b] of the cut margin's variance a x (pa ^ b + pb ^ b), pa and pb the two sides' expected points before the
    cut (each side's points spread with its own; a lopsided game's margin spreads less than an even one's at the same
    total), fitted on logs (b on a grid, a its best then) over
    `games` ((home, away) under `world`) at each of CUT_TARGETS: the margin at the cut (sim_draw.draw_game's
    until_cut) around its expected value, over `draws` draws each (sim_closing_means.cut_sd reads it)."""
    from services.scoring import scoring_for
    from services.sim_centre import centred
    from services.sim_draw import draw_game
    from services.sim_game import _mean_points, _side, _side_means
    rng, rows = np.random.default_rng(seed), []
    for home, away in games:
        for target in CUT_TARGETS:
            sides = centred({home: _side(world, home, away, set(), {}), away: _side(world, away, home, set(), {})},
                            {home: float(target[0]), away: float(target[1])})
            m = {t: _side_means(s) for t, s in sides.items()}
            before = (_mean_points(m[home], m[away]), _mean_points(m[away], m[home]))
            cuts = np.array([draw_game((sides[home], sides[away]), rng, scoring_for("half"), until_cut=True)
                             for _ in range(draws)], dtype=float)
            rows.append((*before, float(np.var(cuts[:, 0] - cuts[:, 1] - (before[0] - before[1])))))
    return fit_cut_var(np.array(rows))


def fit_cut_var(rows: np.ndarray) -> list[float]:
    """[a, b] of var = a x (pa ^ b + pb ^ b) over rows of (pa, pb, variance), least squares on logs."""
    pa, pb, var = (np.maximum(rows[:, j], 1.0) for j in range(3))

    def misfit(b: float) -> tuple[float, float]:
        shape = np.log(pa ** b + pb ** b)
        log_a = float(np.mean(np.log(var) - shape))
        return float(np.sum((np.log(var) - shape - log_a) ** 2)), log_a
    best = min(np.arange(0.2, 1.5001, 0.005), key=lambda b: misfit(b)[0])
    return [round(float(np.exp(misfit(best)[1])), 3), round(float(best), 4)]


def _cut_var(session) -> list[float]:
    """cut_spread on CUT_SEASON's belief before CUT_WEEK, the week's first CUT_GAMES games. Its games are centred
    through sim_closing_means on the stored sim_closing.json: `write` stores the new table first, then measures."""
    from services import replay_pool, sim_world
    data = replay_pool.load_season_data(session, CUT_SEASON, "half")
    games = [(g.home_team, g.away_team) for g in data.schedule.games if g.week == CUT_WEEK][:CUT_GAMES]
    return cut_spread(sim_world.estimate(data, CUT_WEEK), games)


def fit(seasons: Iterable[int], window: int = WINDOW, refresh: bool = False) -> dict:
    from database.db import SessionLocal

    with SessionLocal() as session:
        script = line_script(session, seasons)
    try:
        from services import sim_closing
        cut_var = list(sim_closing.stored()["cut_var"])      # the stored one, re-measured by `write` on the new table
    except RuntimeError:
        log.warning("no stored cut_var to start from (%s unreadable or incomplete): using %s",
                    CLOSING_PATH.name, CUT_VAR_START, exc_info=True)
        cut_var = list(CUT_VAR_START)
    every = scoring_games(seasons, refresh)
    # the endings need every scoring play. Overtime reads every game: a tied one shows in the score alone; a
    # decided one still needs its quarter-5 play (one with no game clock is lost: games_of logs those)
    games = with_plays([g for g in every if consistent(g)], window, seasons, refresh)
    table = endings(games, window)
    return {"seasons": [min(g["season"] for g in games), max(g["season"] for g in games)], "games": len(games),
            "window": window, "events": list(EVENTS), "max_margin": MAX_MARGIN,
            "td_yards": td_yards(games, window),
            "table": {str(k): sorted([[n, list(t), list(lead), spread] for (t, lead, spread), n in rows.items()], reverse=True)
                      for k, rows in sorted(table.items())},
            "ot": overtime(every), "line_script": script, "cut_var": cut_var, "check": check(games, window)}


def write(result: dict) -> dict:
    """Store `result`, then measure its cut_var on the table just stored (the measurement centres games through
    it) and store it again: one --fit is enough, and a file without cut_var can be refit."""
    from database.db import SessionLocal
    from services import sim_closing, sim_closing_means, sim_script
    caches = (sim_closing.stored, sim_closing_means._endings, sim_closing_means._tables, sim_closing_means._at,
              sim_script._window)
    previous = CLOSING_PATH.read_text(encoding="utf-8") if CLOSING_PATH.exists() else None
    try:
        _store(dumps(result))
        for cache in caches:
            cache.cache_clear()
        with SessionLocal() as session:
            result = {**result, "cut_var": _cut_var(session)}
        _store(dumps(result))
    except BaseException:           # a failed measurement must not leave a new table beside the old cut_var
        if previous is None:
            CLOSING_PATH.unlink(missing_ok=True)
        else:
            _store(previous)
        raise
    finally:
        for cache in caches:
            cache.cache_clear()
    return result


def _store(text: str) -> None:
    partial = CLOSING_PATH.with_suffix(f".{os.getpid()}.tmp")
    try:
        partial.write_text(text, encoding="utf-8")
        os.replace(partial, CLOSING_PATH)
    except BaseException:                             # a locked file: leave no temp file beside the module
        partial.unlink(missing_ok=True)
        raise


def dumps(result: Mapping) -> str:
    """sim_closing.json's text: one ending row per line, so a refit's diff is readable."""
    table = result["table"]
    head = json.dumps({k: v for k, v in result.items() if k != "table"}, indent=1)[:-2]
    margins = ",\n".join(f' "{k}": [\n' + ",\n".join(f"  {json.dumps(row)}" for row in rows) + "\n ]"
                         for k, rows in table.items())
    return f'{head},\n "table": {{\n{margins}\n }}\n}}\n'


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fit", action="store_true", help="measure and write sim_closing.json")
    parser.add_argument("--seasons", default=f"{SEASONS.start}-{SEASONS.stop - 1}")
    parser.add_argument("--refresh", action="store_true", help="download the play-by-play again")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    first, _, last = args.seasons.partition("-")
    result = fit(range(int(first), int(last or first) + 1), refresh=args.refresh)
    if args.fit:
        result = write(result)
        log.info("wrote %s", CLOSING_PATH.name)
    log.info("%d games, window %ds", result["games"], result["window"])
    log.info("overtime %s", result["ot"])
    log.info("volume per point of expected margin %s", result["line_script"])
    log.info("cut margin variance%s = %s x (pa ^ %s + pb ^ b), pa / pb a side's expected points before the cut",
             "" if args.fit else " (stored, not re-measured)", *result["cut_var"])
    for name, row in result["check"].items():
        log.info("%-15s %s", name, "  ".join(f"{k} {100 * v:.1f}%" for k, v in row.items()))


if __name__ == "__main__":
    main()
