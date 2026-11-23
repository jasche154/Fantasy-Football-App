"""Simulated seasons: the realism gate. Compares simulated seasons with the real ones they stand in for.

    python -m services.sim_eval --fit [--seasons 2021-2025]        # measure sim_params.json (sim_fit)
    python -m services.sim_eval [--seasons 2021-2025] [--seeds 3] [--mode mid|full|both]
                                [--set talent.role_sd=0.4 ...] [--save-tuned] [--out report.json]

- mid: base = the real season as known before week MID_WEEK (its as_of copy plus the full schedule); weeks
  MID_WEEK..last are simulated and compared with the real ones (sim_metrics.weekly). This is out of sample:
  the simulation never sees the weeks it is judged on.
- full: season S built exactly the way a future season is (sim_setup._future_base): the belief at the end of
  S-1 (estimate, all of it) through sim_next_year.next_year with S's real rookies (nflverse draft picks of
  year S, landing on the drafting team; needs the network) and S's real veteran moves (`real_moves`), S's
  real schedule; the whole season is simulated and compared with the real one (sim_metrics.season, weekly,
  rookies, and how our ADP agrees with S's real ADP).
- --set overrides a sim_params.json value for this run (section.key=value, JSON value); --save-tuned writes
  the overrides into the file's `tuned` section, which is how tuned constants get there.

Read-only on the database; the report is logged (and written as JSON with --out).
"""
import argparse
import json
import logging
import math
import time
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

from services import replay_pool, sim_fit, sim_metrics, sim_season, sim_world
from services.replay_pool import SeasonData
from services.sim_world import World

log = logging.getLogger(__name__)

MID_WEEK = 12
SCORING = "half"


def _seasons(text: str) -> list[int]:
    if "-" in text:
        first, last = text.split("-")
        return list(range(int(first), int(last) + 1))
    return [int(s) for s in text.split(",")]


def cut(real: SeasonData, week: int) -> SeasonData:
    """The real season as known before `week`, with the full schedule (the mid-season base)."""
    return replace(real.as_of(week), schedule=real.schedule, known_before=None)


def real_moves(belief: World, real: SeasonData) -> dict[int, str | None]:
    """{player: his team in `real`, or None = gone} for the belief's players whose team changed: a player's
    first team in the season (else his ADP team) differs from his belief team; nobody with no game and no
    ADP in it is left in the league."""
    moves: dict[int, str | None] = {}
    for pid, player in belief.players.items():
        games, listed = real.team_by_week.get(pid, ()), real.players.get(pid)
        team = games[0][1] if games else listed.adp_team if listed is not None and listed.adp is not None else None
        if games or (listed is not None and listed.adp is not None):
            if team is not None and team != player.team:
                moves[pid] = team
        else:
            moves[pid] = None
    return moves


def next_season(session, real: SeasonData, offseason: dict) -> tuple[SeasonData, World, set[int]]:
    """(base, prior, rookie ids) for season `real.season` built like sim_setup._future_base from the season before it."""
    from services import sim_next_year, sim_next_year_fit as fit

    previous = replay_pool.load_season_data(session, real.season - 1, SCORING)
    world = sim_world.estimate(previous, previous.last_week + 1)
    rookies = {pid: r for pid, r in fit.draft_class(session, real.season, offseason["picks"]).items()
               if pid not in world.players}
    experience = fit.experience(session, real.season - 1, offseason["picks"], offseason["first"])
    belief, pool = sim_next_year.next_year(world, previous.players, experience, rookies, real_moves(world, real),
                                           sim_metrics.qb_starts(previous))
    base = sim_next_year.season_base(real.season, belief, pool, real.schedule, SCORING, None, real.defense_prior)
    return base, belief, set(rookies) & set(base.players)


def _mean(rows: list[dict[str, float]]) -> dict[str, float]:
    sums: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        for key, value in row.items():
            if not math.isnan(value):
                sums[key].append(value)
    return {key: sum(v) / len(v) for key, v in sums.items()}


def _offseason(session, seasons: list[int]) -> dict:
    from services import sim_next_year_fit as fit, sim_setup

    return {"picks": fit.draft_picks(range(fit.DRAFT_HISTORY_FROM, max(seasons) + 1)),
            "first": sim_setup.first_seasons(session)}


def _mid(session, season: int, seeds: list[int]) -> tuple[dict, dict]:
    real = replay_pool.load_season_data(session, season, SCORING)
    weeks = range(MID_WEEK, real.last_week + 1)
    base = cut(real, MID_WEEK)
    sims = [sim_metrics.weekly(sim_season.simulate(base, seed, MID_WEEK), weeks) for seed in seeds]
    return sim_metrics.weekly(real, weeks), _mean(sims)


def _whole(data: SeasonData, rookies: set[int], real: SeasonData) -> dict[str, float]:
    weeks = range(1, data.last_week + 1)
    return {**sim_metrics.season(data), **sim_metrics.rookies(data, rookies),
            **sim_metrics.adp_agreement(data, real), **sim_metrics.weekly(data, weeks)}


def _full(session, season: int, seeds: list[int], offseason: dict) -> tuple[dict, dict]:
    real = replay_pool.load_season_data(session, season, SCORING)
    base, prior, rookies = next_season(session, real, offseason)
    sims = [_whole(sim_season.simulate(base, seed, 1, prior), rookies, real) for seed in seeds]
    return _whole(real, rookies, real), _mean(sims)


def _table(title: str, real: dict, sim: dict) -> list[str]:
    lines = [f"== {title} ==", f"{'metric':28s} {'real':>9s} {'sim':>9s} {'diff':>8s}"]
    for key in real:
        if key in sim:
            lines.append(f"{key:28s} {real[key]:9.2f} {sim[key]:9.2f} {sim[key] - real[key]:+8.2f}")
    return lines


def apply_overrides(pairs: list[str]) -> dict[str, dict]:
    """Set section.key=value overrides on the loaded params; returns them as {section: {key: value}}."""
    overrides: dict[str, dict] = defaultdict(dict)
    for pair in pairs:
        name, value = pair.split("=", 1)
        section, key = name.split(".", 1)
        overrides[section][key] = json.loads(value)
        sim_world.params().setdefault(section, {})[key] = json.loads(value)
    return dict(overrides)


def run(seasons: list[int], seeds: list[int], mode: str) -> dict:
    from database.db import SessionLocal

    report = {}
    with SessionLocal() as session:
        offseason = _offseason(session, seasons) if mode in ("full", "both") else {}
        for kind, runner in (("mid", _mid), ("full", lambda *a: _full(*a, offseason))):
            if mode not in (kind, "both"):
                continue
            reals, sims = [], []
            for season in seasons:
                started = time.perf_counter()
                real, sim = runner(session, season, seeds)
                reals.append(real)
                sims.append(sim)
                log.info("%s %s: %d seeds in %.1fs", kind, season, len(seeds), time.perf_counter() - started)
            report[kind] = {"real": _mean(reals), "sim": _mean(sims)}
            for line in _table(f"{kind} ({seasons[0]}-{seasons[-1]}, {len(seeds)} seeds)", *report[kind].values()):
                log.info(line)
    return report


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seasons", default="2021-2025")
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--mode", choices=("mid", "full", "both"), default="both")
    parser.add_argument("--fit", action="store_true", help="measure sim_params.json from the real seasons")
    parser.add_argument("--set", action="append", default=[], help="section.key=value override (JSON value)")
    parser.add_argument("--save-tuned", action="store_true", help="write the --set overrides as tuned values")
    parser.add_argument("--out", help="write the report as JSON here")
    args = parser.parse_args(argv)
    seasons = _seasons(args.seasons)
    if args.fit:
        from database.db import SessionLocal
        with SessionLocal() as session:
            sim_fit.write(sim_fit.fit(session, seasons))
        sim_world.params.cache_clear()
        return
    overrides = apply_overrides(args.set)
    if args.save_tuned:
        stored = json.loads(sim_world.PARAMS_PATH.read_text())
        tuned = stored.pop("tuned", {})
        for section, values in overrides.items():
            tuned.setdefault(section, {}).update(values)
        sim_fit.write(stored, tuned)
        return
    report = run(seasons, list(range(1, args.seeds + 1)), args.mode)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
