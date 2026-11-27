"""Simulated seasons: how a team's pass / run mix moves through the season (writes and reads sim_calendar.json).

    python -m services.sim_calendar --fit [--seasons 2018-2025]

Real teams throw more early in a season and run more late (weather, and late-season football): per team-game,
relative to each season's own mean, 2018-2025, targets run about 2% above it in weeks 1-4 and 2% below it in weeks
15-18, carries the reverse, and yards per target fall about 1% late, while points stay flat. The belief learns a
team's volume without the calendar (sim_teams.team_forms divides each game's targets and carries by its week's
factor), and a game of week W applies W's factors (`factors`: volumes, and yards per catch by the yards per target
factor) before it is centred on its line (sim_centre), so its points stay the line's and only the mix moves.
shortcut: player rates are learned with the calendar in them (a mid-season belief's yards per catch carry the
early weeks' ~0.5% lift); de-season them in sim_world._efficiency if the late-season passing level drifts. Without it, a season simulated from week 12 kept the early-season passing (weeks 12-18 of 2019-2025:
simulated 7 yards a team-game above weeks 1-11 where real teams were 7 below).

`buckets`: for each BUCKETS range, the mean over seasons of (the bucket's mean per team-game / the season's mean),
for targets and carries, and of its receiving yards per target against the season's; from player_stats_weekly.
"""
import argparse
import json
import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping
from functools import lru_cache
from pathlib import Path

import numpy as np

from services.sim_constants import fitted_json

log = logging.getLogger(__name__)

CALENDAR_PATH = Path(__file__).with_name("sim_calendar.json")
CALENDAR_FIT = "python -m services.sim_calendar --fit"
BUCKETS = ((1, 4), (5, 8), (9, 11), (12, 14), (15, 18))
SEASONS = "2018-2025"


@lru_cache(maxsize=1)
def stored() -> Mapping:
    """sim_calendar.json; one clear error naming the file and the fit when its buckets are not BUCKETS."""
    found = fitted_json(CALENDAR_PATH, CALENDAR_FIT, ("buckets",))
    weeks = [tuple(b.get("weeks", ())) for b in found["buckets"]]
    if weeks != [tuple(b) for b in BUCKETS]:
        raise RuntimeError(f"{CALENDAR_PATH.name} has buckets {weeks}, not {list(BUCKETS)}: run {CALENDAR_FIT}")
    return found


def factors(week: int) -> tuple[float, float, float]:
    """(targets, carries, yards per target) multipliers of a game in `week` against a team's calendar-free volume
    and a player's season rates."""
    for (low, high), values in zip(BUCKETS, stored()["buckets"]):
        if low <= week <= high:
            return float(values["targets"]), float(values["carries"]), float(values["ypt"])
    return 1.0, 1.0, 1.0


def fit(session, seasons: Iterable[int]) -> dict:
    from sqlalchemy import text

    wanted = set(seasons)
    games = {(s, w): n for s, w, n in session.execute(text(
        "select season, week, count(*) from team_game_stats group by season, week"))}
    per_week: dict[int, dict[int, tuple[float, float, float]]] = defaultdict(dict)
    for season, week, targets, carries, yards in session.execute(text(
            "select season, week, sum(coalesce(targets, 0)), sum(coalesce(carries, 0)), "
            "sum(coalesce(receiving_yards, 0)) from player_stats_weekly where season_type = 'REG' group by season, week")):
        if season in wanted and games.get((season, week)) and targets:
            n = games[(season, week)]
            per_week[season][week] = (targets / n, carries / n, yards / targets)
    buckets = []
    for low, high in BUCKETS:
        ratios = []
        for weeks in per_week.values():
            mean = np.mean(list(weeks.values()), axis=0)
            inside = [v for w, v in weeks.items() if low <= w <= high]
            if inside:
                ratios.append(np.mean(inside, axis=0) / mean)
        targets, carries, ypt = np.mean(ratios, axis=0)
        buckets.append({"weeks": [low, high], "targets": round(float(targets), 4), "carries": round(float(carries), 4),
                        "ypt": round(float(ypt), 4)})
    return {"seasons": sorted(wanted), "buckets": buckets}


def main(argv: list[str] | None = None) -> None:
    from database.db import SessionLocal
    from services.replay_data import parse_seasons     # the CLI's only use of the importer

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fit", action="store_true", help="measure and write sim_calendar.json")
    parser.add_argument("--seasons", default=SEASONS)
    args = parser.parse_args(argv)
    with SessionLocal() as session:
        result = fit(session, parse_seasons(args.seasons))
    for bucket in result["buckets"]:
        log.info("weeks %s: targets x %.3f, carries x %.3f, yards per target x %.3f", bucket["weeks"],
                 bucket["targets"], bucket["carries"], bucket["ypt"])
    if args.fit:
        CALENDAR_PATH.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
        log.info("wrote %s", CALENDAR_PATH.name)


if __name__ == "__main__":
    main()
