"""Simulated seasons: the realism gate. Compares simulated seasons with the real ones they stand in for.

    python -m services.sim_eval --fit [--seasons 2021-2025]        # measure sim_params.json (sim_fit)
    python -m services.sim_eval [--seasons 2021-2025] [--seeds 3] [--mode mid|full|both]
                                [--set talent.role_sd=0.4 ...] [--save-tuned] [--out report.json]
                                [--workers N] [--refresh]

- mid: base = the real season as known before week MID_WEEK (its as_of copy plus the full schedule); weeks
  MID_WEEK..last are simulated and compared with the real ones (sim_metrics.weekly). This is out of sample:
  the simulation never sees the weeks it is judged on.
- full: season S built exactly the way a future season is (sim_setup._future_base): the belief at the end of
  S-1 (estimate, all of it, on the belief at the end of S-2 as its prior) through sim_next_year.next_year with
  S's real rookies (nflverse draft picks of year S, landing on the drafting team; cached, see below) and
  S's real veteran moves (`real_moves`), S's real schedule, the market's ADP leaning on S-1's track record and
  paying the rookie premium; the whole season is simulated and compared with the real one (sim_metrics.season
  with its leaderboard, weekly, rookies, how our ADP agrees with S's real ADP, all of it and the rookies':
  ours priced in the real list's format, PPR; and how the preseason experts agree with the ADP).
  mid reports the leaderboard of the simulated weeks too.
- --set overrides a sim_params.json value for this run (section.key=value, JSON value); --save-tuned writes
  the overrides into the file's `tuned` section, which is how tuned constants get there.

- --workers: each (mode, season) runs in its own process (default: one per CPU, at most one per case; 1 = all
  in this process, for debugging); the report is the same either way (`run`). The draft picks full mode needs
  are cached in backend/data/cache/ (sim_draft_picks): the network is needed the first time, or
  with --refresh.

Read-only on the database; the report is logged (and written as JSON with --out).
"""
import argparse
import json
import logging
import math
import os
import time
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from functools import partial
from pathlib import Path

from services import replay_pool, sim_fit, sim_market, sim_metrics, sim_season, sim_setup, sim_world
from services.replay_adp_format import TWO_QB
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


def season_end(session, season: int) -> World | None:
    """The belief at the end of `season` (the prior the season after it is estimated on), None if not stored."""
    if season not in replay_pool.SUPPORTED_SEASONS:
        return None
    data = replay_pool.load_season_data(session, season, SCORING)
    return sim_world.estimate(data, data.last_week + 1)


def next_belief(session, real: SeasonData, offseason: dict) -> tuple[World, dict, dict]:
    """(belief, pool, {id: rookie}) going into season `real.season`, built like sim_setup._future_base from the
    season before it with that season's real rookies and moves."""
    from services import sim_next_year, sim_next_year_fit as fit

    previous = replay_pool.load_season_data(session, real.season - 1, SCORING)
    world = sim_world.estimate(previous, previous.last_week + 1, season_end(session, real.season - 2))
    rookies = {pid: r for pid, r in fit.draft_class(session, real.season, offseason["picks"]).items()
               if pid not in world.players}
    experience = fit.experience(session, real.season - 1, offseason["picks"], offseason["first"])
    belief, pool = sim_next_year.next_year(world, previous.players, experience, rookies, real_moves(world, real),
                                           sim_next_year.starts_by_team(previous))
    return belief, pool, rookies


def market_inputs(session, real: SeasonData) -> tuple[str, dict[int, float]]:
    """(scoring, track record) our ADP is priced on to compare with `real`'s list (the stored lists are PPR,
    read for a half league too)."""
    listed = "ppr" if real.adp_format == TWO_QB else real.adp_format
    return listed, sim_market.track_record(replay_pool.load_season_data(session, real.season - 1, listed))


def next_season(session, real: SeasonData, offseason: dict) -> tuple[SeasonData, World, set[int]]:
    """(base, prior, rookie ids) for season `real.season` built like sim_setup._future_base from the season before it."""
    from services import sim_next_year

    belief, pool, rookies = next_belief(session, real, offseason)
    base = sim_next_year.season_base(real.season, belief, pool, real.schedule, SCORING, None, real.defense_prior)
    listed, record = market_inputs(session, real)          # our ADP priced like the real list it is compared with
    priced = sim_next_year.with_market_adp(replace(base, scoring=listed, adp_format=real.adp_format), belief,
                                           record=record, premium=sim_next_year.rookie_premium(rookies))
    return replace(base, players=priced.players), belief, set(rookies) & set(base.players)


def _mean(rows: list[dict[str, float]]) -> dict[str, float]:
    sums: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        for key, value in row.items():
            if not math.isnan(value):
                sums[key].append(value)
    return {key: sum(v) / len(v) for key, v in sums.items()}


def _offseason(session, seasons: list[int], refresh: bool = False) -> dict:
    from services import sim_next_year_fit as fit, sim_setup

    return {"picks": fit.draft_picks(range(fit.DRAFT_HISTORY_FROM, max(seasons) + 1), refresh),
            "first": sim_setup.first_seasons(session)}


def _mid(session, season: int, seeds: list[int]) -> tuple[dict, dict]:
    real = replay_pool.load_season_data(session, season, SCORING)
    weeks = range(MID_WEEK, real.last_week + 1)
    base = cut(real, MID_WEEK)
    rookies = [pid for pid, first in sim_setup.first_seasons(session).items() if first == season]
    sims = [_rest(sim_season.simulate(base, seed, MID_WEEK, rookies=rookies), weeks) for seed in seeds]
    return _rest(real, weeks), _mean(sims)


def _rest(data: SeasonData, weeks: range) -> dict[str, float]:
    return {**sim_metrics.weekly(data, weeks), **sim_metrics.leaderboard(data, weeks)}


def _whole(data: SeasonData, rookies: set[int], real: SeasonData) -> dict[str, float]:
    weeks = range(1, data.last_week + 1)
    return {**sim_metrics.season(data), **sim_metrics.rookies(data, rookies),
            **sim_metrics.adp_agreement(data, real, rookies), **sim_metrics.expert_agreement(data),
            **sim_metrics.weekly(data, weeks)}


def _full(session, season: int, seeds: list[int], offseason: dict) -> tuple[dict, dict]:
    real = replay_pool.load_season_data(session, season, SCORING)
    base, prior, rookies = next_season(session, real, offseason)
    sims = [_whole(sim_season.simulate(base, seed, 1, prior, rookies), rookies, real) for seed in seeds]
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
        sim_world.override(section, key, json.loads(value))
    return dict(overrides)


def _start(overrides: dict[str, dict], offseason: dict) -> None:
    """A worker process's setup (spawned: nothing is inherited): logging, the --set overrides, the offseason
    inputs and its own database session."""
    from database.db import SessionLocal

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for section, values in overrides.items():
        for key, value in values.items():
            sim_world.override(section, key, value)
    from multiprocessing.util import Finalize

    session = SessionLocal()
    Finalize(None, session.close, exitpriority=10)  # closed at worker exit, under fork (Linux) and spawn alike
    _worker.update(offseason=offseason, session=session)


_worker: dict = {}          # a worker process's offseason inputs and session (_start)


def _case(case: tuple[str, int], seeds: list[int], session=None, offseason: dict | None = None) -> tuple[dict, dict]:
    """(real, sim) of one (mode, season): every seed of it in one process, so its seasons load once."""
    kind, season = case
    if session is None:                             # in a worker: its own (_start)
        session, offseason = _worker["session"], _worker["offseason"]
    started = time.perf_counter()
    result = _mid(session, season, seeds) if kind == "mid" else _full(session, season, seeds, offseason)
    log.info("%s %s: %d seeds in %.1fs", kind, season, len(seeds), time.perf_counter() - started)
    return result


def run(seasons: list[int], seeds: list[int], mode: str, workers: int = 1, overrides: dict | None = None,
        refresh: bool = False, case: Callable[..., tuple[dict, dict]] = _case) -> dict:
    """The report. Each (mode, season) is one case; with workers > 1 the cases run in a process pool (each worker
    gets the overrides and offseason inputs explicitly and opens its own session, see _start), full cases first
    as they take longest. Results are gathered by case and averaged in the fixed mode / season / seed order, never
    in completion order, so the report is identical to the serial one (workers = 1, all in this process). `case`
    (a module-level function, so a worker can import it) is swappable for tests."""
    from database.db import SessionLocal

    kinds = [kind for kind in ("mid", "full") if mode in (kind, "both")]
    cases = [(kind, season) for kind in reversed(kinds) for season in seasons]
    workers = min(workers, len(cases))
    with SessionLocal() as session:
        offseason = _offseason(session, seasons, refresh) if "full" in kinds else {}
        if workers <= 1:
            results = {key: case(key, seeds, session, offseason) for key in cases}
    if workers > 1:
        with ProcessPoolExecutor(workers, initializer=_start, initargs=(overrides or {}, offseason)) as pool:
            # a case that fails raises here; map's own iterator cancels the queued ones
            results = dict(zip(cases, pool.map(partial(case, seeds=seeds), cases)))
    report = {}
    for kind in kinds:
        pairs = [results[(kind, season)] for season in seasons]
        report[kind] = {"real": _mean([real for real, _ in pairs]), "sim": _mean([sim for _, sim in pairs])}
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
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1,
                        help="processes for the (mode, season) cases (default: one per CPU; 1 = serial)")
    parser.add_argument("--refresh", action="store_true", help="download the draft picks again (else cached)")
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
    report = run(seasons, list(range(1, args.seeds + 1)), args.mode, args.workers, overrides, args.refresh)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
