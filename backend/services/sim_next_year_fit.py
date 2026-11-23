"""Simulated seasons: fit sim_next_year.json (the offseason model, services/sim_next_year.py) from real seasons.

    python -m services.sim_next_year_fit --fit [--seasons 2019-2025] [--dump rows.json]

For each season S, the public belief at the end of S-1 and of S (sim_world.estimate over the whole season,
no prior) are compared:
- rookies (nflverse draft picks of year S, through nflreadpy, joined to the players table by gsis id): their
  role in S as a line in log(pick) per position (targets, carries, a kicker's starts, a QB's chance to start
  week 1), fitted by least squares on every rookie (a never-seen late pick counts as no role), so the mean
  is right; a position with fewer than MIN_ROOKIES gets its mean. Also rookie QBs' passer quality and how
  often a top-64 rookie QB who did not start week 1 started a game later (`qb_takeover`, the target for
  sim_talent's rookie takeover);
- veterans with GAMES_MIN games and a relevant role (VOLUME_MIN) this season and NEXT_GAMES_MIN games the next
  (selected on this season only: requiring a relevant role next season too would drop the players who fell
  off and flatten the slope): log volume next season (floored at VOLUME_FLOOR) on log volume this season
  (centered at `center`) plus an experience-bucket effect (`age`, centered so only the shape counts:
  sim_next_year rescales each team) per position; the slope is `keep` (at most 1: the floor and the
  selection on this season can push it just past 1, which would spread roles apart every year);
- efficiency persistence: next season's RAW rate (box totals; the belief is shrunk, which would bias the
  slope) on this season's belief, as deviations from the position norm (`efficiency_keep`). QB quality and
  INT rate have no raw rate in the box lines, so theirs is the belief's slope over its mean shrink;
- teams: volume and defense factors next season on this season's, as log deviations (`team_keep`).
Experience = seasons through S-1 since his draft year (nflverse), else since his first database season.

Needs the network (nflreadpy) and the database; never runs in the app.
"""
import argparse
import json
import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path

import numpy as np
from sqlalchemy import select

from integrations.weekly_sources import TEAM_ALIASES
from services import replay_pool, sim_world
from services.sim_metrics import qb_starts
from services.replay_pool import SeasonData
from services.sim_next_year import PATH, VOLUME_MIN, volume
from services.sim_offseason import Rookie
from services.sim_schedule import TEAMS
from services.sim_setup import first_seasons

log = logging.getLogger(__name__)

SCORING = "half"
POSITIONS = ("QB", "RB", "WR", "TE")
EXPERIENCE_EDGES = (1, 2, 3, 4, 6, 9)           # experience buckets: 1, 2, 3, 4-5, 6-8, 9+ seasons
GAMES_MIN = 6
NEXT_GAMES_MIN = 3
VOLUME_FLOOR = 0.5                              # opportunities per game: a vanished role counts as this
MIN_ROOKIES = 30
TAKEOVER_PICKS = 64
DRAFT_HISTORY_FROM = 1995
RAW = {"catch": ("rec", "targets", 20), "ypc": ("rec_yds", "rec", 15), "rec_td": ("rec_td", "rec", 15),
       "ypr": ("rush_yds", "carries", 30), "rush_td": ("rush_td", "carries", 30)}    # numerator, volume, minimum
QB_SHRINK = {"quality": 150.0, "int_rate": 250.0}     # sim_world.EFFICIENCY_PRIOR, in pass attempts
TEAM_FIELDS = ("targets", "carries", "pass_def", "rush_def", "takeaways")


def draft_picks(seasons: Iterable[int]) -> list[dict]:
    """nflverse draft picks: season, pick, team (schedule code), gsis_id, position, name, college."""
    import nflreadpy as nfl

    rows = nfl.load_draft_picks(sorted(set(seasons))).to_dicts()
    return [{"season": int(r["season"]), "pick": int(r["pick"]), "team": TEAM_ALIASES.get(r["team"], r["team"]),
             "gsis_id": r["gsis_id"], "position": r["position"], "name": r["pfr_player_name"],
             "college": r.get("college") or "-"} for r in rows if r.get("gsis_id")]


def _ids(session, gsis: Iterable[str]) -> dict[str, int]:
    from database.models import Player

    return dict(session.execute(select(Player.gsis_id, Player.id).where(Player.gsis_id.in_(list(gsis)))).all())


def draft_class(session, season: int, picks: list[dict]) -> dict[int, Rookie]:
    """{player id: Rookie} of the skill players and kickers drafted in `season` (landing team = drafting team)."""
    mine = [r for r in picks if r["season"] == season and r["position"] in (*POSITIONS, "K") and r["team"] in TEAMS]
    ids = _ids(session, (r["gsis_id"] for r in mine))
    return {ids[r["gsis_id"]]: Rookie(r["name"], r["position"], r["team"], r["pick"], r["college"])
            for r in mine if r["gsis_id"] in ids}


def experience(session, season: int, picks: list[dict], first: Mapping[int, int]) -> dict[int, int]:
    """{player id: seasons played through `season`}: since his draft year when known, else his first season."""
    drafted = _ids(session, (r["gsis_id"] for r in picks))
    rookie_year = {drafted[r["gsis_id"]]: r["season"] for r in picks if r["gsis_id"] in drafted}
    out = {}
    for pid in {*first, *rookie_year}:
        start = rookie_year.get(pid, first.get(pid))
        if start is not None and start <= season:
            out[pid] = season - start + 1
    return out


def _raw(data: SeasonData) -> dict[int, dict[str, float]]:
    totals: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for (pid, _), line in data.box.items():
        for name in ("targets", "rec", "rec_yds", "rec_td", "carries", "rush_yds", "rush_td"):
            totals[pid][name] += getattr(line, name)
    return totals


def _rookie_rows(season: int, rookies: Mapping[int, Rookie], new: sim_world.World, starts: Mapping) -> list[dict]:
    rows = []
    for pid, rookie in rookies.items():
        p = new.players.get(pid)
        rows.append({"position": rookie.position, "pick": rookie.pick, "targets": p.targets if p else 0.0,
                     "carries": p.carries if p else 0.0, "starts": p.starts if p else 0.0,
                     "quality": p.quality if p else None, "week1": float(1 in starts.get(pid, ())),
                     "started": len(starts.get(pid, ())), "season": season})
    return rows


def observations(session, seasons: list[int]) -> dict[str, list[dict]]:
    """{"rookies", "veterans", "teams": rows} for seasons S (each needs S-1 loaded)."""
    picks = draft_picks(range(DRAFT_HISTORY_FROM, max(seasons) + 1))
    first = first_seasons(session)
    rookies, veterans, teams = [], [], []
    for season in seasons:
        before = replay_pool.load_season_data(session, season - 1, SCORING)
        after = replay_pool.load_season_data(session, season, SCORING)
        old = sim_world.estimate(before, before.last_week + 1)
        new = sim_world.estimate(after, after.last_week + 1)
        rookies += _rookie_rows(season, draft_class(session, season, picks), new, qb_starts(after))
        exp, raw = experience(session, season - 1, picks, first), _raw(after)
        for pid, was in old.players.items():
            now = new.players.get(pid)
            if now is None or was.position not in POSITIONS or pid not in exp:
                continue
            veterans.append({"position": was.position, "experience": exp[pid], "games": (was.games, now.games),
                             "volume": (volume(was), volume(now)), "raw": dict(raw.get(pid, {})),
                             **{name: (getattr(was, name), getattr(now, name)) for name in (*RAW, *QB_SHRINK)}})
        teams += [{name: (getattr(old.teams[t], name), getattr(new.teams[t], name)) for name in TEAM_FIELDS}
                  for t in old.teams if t in new.teams]
        log.info("%s: %d rookies, %d veterans so far", season, len(rookies), len(veterans))
    return {"rookies": rookies, "veterans": veterans, "teams": teams}


def _line(rows: list[dict], field: str) -> list[float]:
    """[intercept, slope] of field on log(pick); the mean when there are too few rookies."""
    y = np.array([float(r[field]) for r in rows])
    if len(rows) < MIN_ROOKIES:
        return [round(float(y.mean()), 5) if len(y) else 0.0, 0.0]
    x = np.log([r["pick"] for r in rows])
    (a, b), *_ = np.linalg.lstsq(np.column_stack([np.ones_like(x), x]), y, rcond=None)
    return [round(float(a), 5), round(float(b), 5)]


def _rookie_fit(rows: list[dict]) -> dict:
    out: dict = {}
    for position in (*POSITIONS, "K"):
        mine = [r for r in rows if r["position"] == position]
        fields = {"QB": ("carries", "week1"), "K": ("starts",)}.get(position, ("targets", "carries"))
        out[position] = {field: _line(mine, field) for field in fields} | {"n": len(mine)}
    benched = [r for r in rows if r["position"] == "QB" and r["pick"] <= TAKEOVER_PICKS and not r["week1"]]
    played = [r["quality"] for r in rows if r["position"] == "QB" and r["started"] >= 3 and r["quality"]]
    out["qb_takeover"] = round(sum(1 for r in benched if r["started"]) / max(1, len(benched)), 4)
    out["qb_takeover_n"] = len(benched)
    out["qb_quality"] = round(float(np.mean(played)), 4) if played else 0.9
    return out


def _relevant(row: dict) -> bool:
    return min(row["games"]) >= GAMES_MIN and min(row["volume"]) >= VOLUME_MIN[row["position"]]


def _age_fit(rows: list[dict], position: str) -> tuple[dict, float, float]:
    mine = [r for r in rows if r["position"] == position and r["games"][0] >= GAMES_MIN
            and r["volume"][0] >= VOLUME_MIN[position] and r["games"][1] >= NEXT_GAMES_MIN]
    x = np.log([r["volume"][0] for r in mine])
    y = np.log([max(r["volume"][1], VOLUME_FLOOR) for r in mine])
    groups = np.array([sum(1 for e in EXPERIENCE_EDGES[1:] if r["experience"] >= e) for r in mine])
    used = [g for g in range(len(EXPERIENCE_EDGES)) if (groups == g).any()]
    center = float(x.mean())
    design = np.column_stack([*(groups == g for g in used), x - center]).astype(float)
    solved, *_ = np.linalg.lstsq(design, y - x, rcond=None)
    effects = dict(zip(used, solved[:-1].tolist()))
    middle = float(np.mean(list(effects.values())))
    change = [round(effects.get(g, effects[max(used)]) - middle, 4) for g in range(len(EXPERIENCE_EDGES))]
    return ({"edges": list(EXPERIENCE_EDGES), "log_change": change,
             "n": [int((groups == g).sum()) for g in range(len(EXPERIENCE_EDGES))]},
            round(min(1.0, 1.0 + float(solved[-1])), 4), round(center, 4))      # never anti-regression


def _efficiency_fit(rows: list[dict]) -> dict:
    norms, league = sim_world.params()["positions"], sim_world.params()["league"]
    out = {}
    for name, (numerator, base, minimum) in RAW.items():
        x, y, w = [], [], []
        for r in rows:
            norm, n = norms[r["position"]].get(name), r["raw"].get(base, 0.0)
            if not _relevant(r) or r["position"] == "QB" or not norm or n < minimum:
                continue
            x.append(r[name][0] / norm - 1)
            y.append(r["raw"][numerator] / n / norm - 1)
            w.append(n)
        x, y, w = np.array(x), np.array(y), np.array(w)
        out[name] = round(float((w * x * y).sum() / (w * x * x).sum()), 4)
    for name, prior in QB_SHRINK.items():
        qbs = [r for r in rows if r["position"] == "QB" and _relevant(r) and r[name][0] > 0 and r[name][1] > 0]
        norm = 1.0 if name == "quality" else league["int_rate"]
        a = np.log([r[name][0] / norm for r in qbs])
        b = np.log([r[name][1] / norm for r in qbs])
        attempts = np.array([r["games"][1] * league["targets"] * league["attempts_per_target"] for r in qbs])
        out[name] = round(float(a @ b / (a @ a)) / float(np.mean(attempts / (attempts + prior))), 4)
    return out


def _team_fit(rows: list[dict]) -> dict:
    league = sim_world.params()["league"]
    out = {}
    for name in TEAM_FIELDS:
        norm = league.get(name, 1.0)
        a = np.log([r[name][0] / norm for r in rows])
        b = np.log([r[name][1] / norm for r in rows])
        out[name] = round(float(a @ b / (a @ a)), 4)
    return out


def summarize(rows: Mapping[str, list[dict]], seasons: list[int]) -> dict:
    age, keep, center = {}, {}, {}
    for position in POSITIONS:
        age[position], keep[position], center[position] = _age_fit(rows["veterans"], position)
    return {"seasons": seasons, "rookies": _rookie_fit(rows["rookies"]), "age": age, "keep": keep,
            "center": center, "efficiency_keep": _efficiency_fit(rows["veterans"]),
            "team_keep": _team_fit(rows["teams"])}


def _seasons(text: str) -> list[int]:
    first, _, last = text.partition("-")
    return list(range(int(first), int(last or first) + 1))


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fit", action="store_true", help="measure and write sim_next_year.json")
    parser.add_argument("--seasons", default="2019-2025")
    parser.add_argument("--dump", help="also write the raw observations here (JSON)")
    args = parser.parse_args(argv)
    if not args.fit:
        parser.print_help()
        return
    from database.db import SessionLocal

    seasons = _seasons(args.seasons)
    with SessionLocal() as session:
        rows = observations(session, seasons)
    if args.dump:
        Path(args.dump).write_text(json.dumps(rows))
    PATH.write_text(json.dumps(summarize(rows, seasons), indent=1) + "\n")
    log.info("wrote %s", PATH.name)


if __name__ == "__main__":
    main()
