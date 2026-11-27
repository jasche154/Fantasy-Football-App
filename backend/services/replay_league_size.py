"""Replay League trade chart by league size: in a league of fewer than 12 teams the players a smaller league leaves
on waivers are priced like waiver players, not like a 12-team league's bench.

The chart's slot curve (replay_chart.slot_curve) comes from ADP lists drafted in 12-team leagues, so it prices
the 12-team depth. A smaller league starts and rosters fewer players: in an 8-team superflex league 16 QBs
start and the 29th is the first one left unrostered, where a 12-team league reaches the 43rd. `rank_map` keeps
each position's starters at their own rank (the ADP market prices weekly starters alike in any league size),
stretches the ranks from the last starter to the first unrostered player onto the 12-team league's (so that
player is priced like the 12-team one) and goes on one rank per rank past it. `depth` counts starters and
rostered players as services.league.replacement_levels does (FLEX and SUPERFLEX filled by the best of the
fitted points-per-game curves, plus each position's share of the bench) on the average points per game by
position rank of real 2018-2025 seasons (PPR, weeks 1, 4, 7, 10 and 13 to week 17, at least 3 games counted:
replay_chart_eval's target rule). `sized(data, league)` moves the ADP of each position's nth drafted player to
the 12-team pick of rank m(n), so the slot curve, every value read off it and the preseason picks follow.

Measured with replay_chart_eval --teams 8 (8 seasons, the player-weeks both charts scored): regular Spearman
0.464 -> 0.475, held-out RMSE 4.022 -> 4.001, out players 2.927 -> 2.889; superflex 0.523 -> 0.522, 4.165 ->
4.155, out players 2.925 -> 2.921 (--teams 10: regular 0.518 -> 0.524, 4.012 -> 3.990, 2.975 -> 2.957; superflex
0.549 -> 0.550, 4.743 -> 4.729, out players 3.477 -> 3.490). Mapping every rank by equal points over replacement
instead (a starter too)
scored worse in superflex (0.540 -> 0.422, out players 3.082 -> 3.326): it marks down the starting QBs, whom the
12-team chart already prices below their points. Leagues of 12 teams or more are left as they are (the brief's
rule: the 12- and 14-team charts are the fitted ones).

`python -m services.replay_league_size --fit` measures the curves (replay_league_size.json). Pure apart from that
CLI.
"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse  # noqa: E402
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace
from functools import lru_cache
from types import MappingProxyType

from services.league import POSITIONS, LeagueSettings, starter_counts
from services.replay_pool import SKILL, SeasonData

log = logging.getLogger(__name__)

PATH = Path(__file__).with_name("replay_league_size.json")
BASE_TEAMS = 12                  # the league size the ADP lists (and so the chart) price
FIT_WEEKS = (1, 4, 7, 10, 13)
FIT_END = 17
FIT_SEASONS = "2018-2025"


@lru_cache(maxsize=1)
def points() -> Mapping[str, tuple[float, ...]]:
    """{position: average points per game of the nth best, non-increasing}."""
    stored = json.loads(PATH.read_text(encoding="utf-8"))
    return MappingProxyType({position: tuple(curve) for position, curve in stored["points"].items()})


def depth(league: LeagueSettings) -> dict[str, tuple[int, int]]:
    """{position: (starters, rostered)} league-wide on the fitted curves, as replacement_levels counts them: its
    starters with FLEX and SUPERFLEX filled by the best of the curves, plus each position's share of the bench."""
    pools = {position: list(points().get(position, ())) for position in POSITIONS}
    starters = starter_counts(pools, league)
    total = sum(starters.values()) or 1
    return {position: (starters[position],
                       starters[position] + round(league.teams * league.bench * starters[position] / total))
            for position in POSITIONS}


@lru_cache(maxsize=32)
def rank_map(league: LeagueSettings) -> Mapping[str, tuple[float, ...]]:
    """{position: (12-team rank of the 1st, 2nd, ... player)} for a league under BASE_TEAMS teams; {} otherwise.
    Starters keep their rank; from the last starter to the first player left unrostered the ranks stretch to the
    12-team league's, so that player is priced like the 12-team one; past it, one rank per rank."""
    if league.teams >= BASE_TEAMS:
        return MappingProxyType({})
    here, base = depth(league), depth(replace(league, teams=BASE_TEAMS))
    found = {}
    for position in SKILL:
        (starters, rostered), (_, rostered_12) = here[position], base[position]
        stretch = (rostered_12 - starters + 1) / (rostered - starters + 1)    # last starter -> first unrostered
        found[position] = tuple(
            float(n) if n <= starters else
            starters + (n - starters) * stretch if n <= rostered + 1 else
            rostered_12 + 1 + (n - rostered - 1)
            for n in range(1, len(points()[position]) + 1))
    return MappingProxyType(found)


def twelve_team_rank(ranks: Sequence[float], n: int) -> float:
    """The 12-team rank of the nth player (1-based); past the fitted curve, one rank per rank from its last."""
    return ranks[n - 1] if n <= len(ranks) else ranks[-1] + (n - len(ranks))


def untapered(league: LeagueSettings) -> LeagueSettings:
    """`league` at BASE_TEAMS teams when it is smaller: the chart without the taper, on the raw-ADP scale (the CPU
    draft prices an out player on it next to healthy players at their raw ADP, replay_draft._out_priced)."""
    return league if league.teams >= BASE_TEAMS else replace(league, teams=BASE_TEAMS)


@lru_cache(maxsize=64)
def sized(data: SeasonData, league: LeagueSettings) -> SeasonData:
    """The season as `league`'s size values it: each skill position's nth drafted player (by ADP) at the pick the
    12-team slot curve gives rank m(n) (`rank_map`); `data` itself for 12 teams or more. Cached per season object."""
    ranks = rank_map(league)
    if not ranks:
        return data
    from services import replay_chart, replay_superflex      # replay_chart imports this module's users

    curve = replay_chart.slot_curve(data)
    moved: dict[int, float] = {}
    for position, mapped in ranks.items():
        drafted = sorted((p.adp, pid) for pid, p in data.players.items()
                         if p.position == position and p.adp is not None)
        for n, (_, pid) in enumerate(drafted, start=1):
            moved[pid] = replay_chart.pick_at(curve, position, twelve_team_rank(mapped, n))
    return replay_superflex._with_adp(data, {pid: moved.get(pid, p.adp) for pid, p in data.players.items()})


# ---------------- fit ----------------

def fit(session, seasons: Sequence[int]) -> dict:
    """The average points per game by position rank (module docstring), each curve made non-increasing."""
    from services import replay_pool
    from services.replay_chart_eval import MIN_GAMES, _games, _rate

    curves: dict[str, list[list[float]]] = {position: [] for position in SKILL}
    for season in seasons:
        data = replay_pool.load_season_data(session, season, "ppr")
        for week in FIT_WEEKS:
            pools: dict[str, list[float]] = {position: [] for position in SKILL}
            for pid, player in data.players.items():
                if player.position in SKILL:
                    games = _games(data, pid, week, FIT_END)
                    if len(games.points) >= MIN_GAMES:
                        pools[player.position].append(_rate(games))
            for position, rates in pools.items():
                curves[position].append(sorted(rates, reverse=True))
        replay_pool.clear_cache()
    found = {}
    for position, lists in curves.items():
        depth = min(len(c) for c in lists)            # every season-week lists this many
        mean = [sum(c[n] for c in lists) / len(lists) for n in range(depth)]
        found[position] = [round(min(mean[:n + 1]), 3) for n in range(depth)]
    return {"seasons": list(seasons), "weeks": list(FIT_WEEKS), "points": found}


def main(argv: list[str] | None = None) -> None:
    from services.replay_data import parse_seasons
    from services.replay_value_eval import DEFAULT_DB, read_only_copy

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fit", action="store_true", help="measure replay_league_size.json")
    parser.add_argument("--seasons", default=FIT_SEASONS)
    args = parser.parse_args(argv)
    if not args.fit:
        parser.print_help()
        return
    with read_only_copy(DEFAULT_DB) as session:
        found = fit(session, parse_seasons(args.seasons))
    PATH.write_text(json.dumps(found, indent=1) + "\n", encoding="utf-8")
    points.cache_clear()
    rank_map.cache_clear()
    sized.cache_clear()
    log.info("Wrote %s: %s", PATH.name, {p: len(c) for p, c in found["points"].items()})


if __name__ == "__main__":
    main()
