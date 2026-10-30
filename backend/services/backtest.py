"""Backtest the weekly model on a past season, using only what was known before each game.

Run from backend/:
    python -m services.backtest --season 2025            # grid search, report, write ranges
    python -m services.backtest --season 2025 --quick    # only the current production settings

For every week from FIRST_WEEK on, each fantasy-relevant player (2+ games so far, rate of
5+ PPR points) is projected from games before that week:
- Season stats are truncated to earlier weeks.
- Defense strength is rebuilt from earlier games only.
- Vegas lines come from the schedule.
- Injury designations come from that week's official report.

A player who doesn't appear in the box score while his team played scores 0, so the
injury layer is judged on the games players actually miss.

The best settings are reported, and the floor/ceiling ratios (10th/90th percentile of actual
over projected points, by position) are written to services/projection_ranges.json.
"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse  # noqa: E402
import itertools
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, replace
from statistics import mean

import numpy as np
from sqlalchemy.orm import Session

from services.injuries import build_context, learned_play_rates  # noqa: F401  (rates cached for the run)
from services.projections import MIN_PRIOR_GAMES, PlayerSeason, load_player_seasons
from services.scoring import scoring_for
from services.schedule_strength import Schedule, defense_strength, load_schedule
from services.weekly_model import RANGES_PATH, WeeklyModelSettings, weekly_rate

log = logging.getLogger("backtest")
FIRST_WEEK, LAST_WEEK = 4, 18
MIN_GAMES_BEFORE = 2
MIN_RATE = 5.0


@dataclass(frozen=True)
class Target:
    week: int
    player_id: int
    position: str
    actual: float
    projected: float
    if_active: float = 0.0
    played: bool = True
    snap_pct: float | None = None       # 0-100, that game


def truncate(player: PlayerSeason, week: int, schedule: Schedule) -> PlayerSeason:
    """Games before `week`, with the team he played for most recently (not today's team)."""
    games = tuple(g for g in player.games if g.week < week)
    team = schedule.opponent(games[-1].opponent, games[-1].week) if games else None
    return replace(player, games=games, team=team or player.team)


def team_ppg_before(schedule: Schedule, team: str, week: int) -> float | None:
    scored = [g.points_scored(team) for g in schedule.games
              if g.week < week and g.is_final and team in (g.home_team, g.away_team)]
    return mean(scored) if scored else None


@dataclass
class SeasonData:
    season: int
    current: list[PlayerSeason]
    priors: dict[int, float]
    schedule: Schedule
    cache: dict


def load_season(session: Session, season: int) -> SeasonData:
    scoring = scoring_for("ppr")
    current = load_player_seasons(session, season, scoring)
    priors = {p.player_id: p.ppg for p in load_player_seasons(session, season - 1, scoring)
              if p.games_played >= MIN_PRIOR_GAMES}
    return SeasonData(season, current, priors, load_schedule(session, season), {})


def project_week(session: Session, data: SeasonData, week: int, settings: WeeklyModelSettings) -> list[Target]:
    season, current, priors, schedule, cache = data.season, data.current, data.priors, data.schedule, data.cache
    if ("before", week) not in cache:
        before = [truncate(p, week, schedule) for p in current]
        cache[("before", week)] = before
        cache[("defense", week)] = defense_strength(before, schedule)
    before = cache[("before", week)]
    eligible = [p for p in before if p.games_played >= MIN_GAMES_BEFORE and p.team]
    defense = cache[("defense", week)] if settings.sos_weight else None
    rates = {p.player_id: weekly_rate(p, priors.get(p.player_id), settings.half_life) for p in eligible}
    context = None
    if settings.use_injuries:
        key = ("context", week, settings.half_life)
        if key not in cache:
            cache[key] = build_context(session, before, rates, season, week, schedule, use_live=False,
                                       exclude_rate_season=season)
        context = cache[key]
    games_this_week = {p.player_id: next((g for g in p.games if g.week == week), None) for p in current}
    played = {pid: (g.fantasy_points if g else None) for pid, g in games_this_week.items()}
    targets = []
    for player in eligible:
        rate = rates[player.player_id]
        game = schedule.game(player.team, week)
        if rate < MIN_RATE or game is None:
            continue                                   # bye week or not fantasy-relevant
        opponent = game.away_team if game.home_team == player.team else game.home_team
        multiplier = 1.0
        if defense is not None:
            multiplier *= defense.factor(opponent, player.position) ** settings.sos_weight
        implied, team_ppg = game.implied_points(player.team), team_ppg_before(schedule, player.team, week)
        if settings.implied_weight and implied and team_ppg:
            multiplier *= min(1.25, max(0.75, implied / team_ppg)) ** settings.implied_weight
        if settings.use_factors:
            multiplier *= _factor(session, data, week, player.position, player.team, opponent)
        if_active = rate * multiplier
        projected = if_active
        if context is not None:
            if_active += sum(b.expected_points for b in context.boosts.get(player.player_id, ())) * multiplier
            projected = if_active * context.of(player.player_id).play_prob
        actual = played.get(player.player_id)
        game_line = games_this_week.get(player.player_id)
        targets.append(Target(week, player.player_id, player.position or "", actual or 0.0, projected,
                              if_active, actual is not None, game_line.snap_pct if game_line else None))
    return targets


def _factor(session: Session, data: SeasonData, week: int, position: str | None, team: str | None,
            opponent: str) -> float:
    """Calibration / EPA matchup / weather factor, from data before the week (services/projection_factors.py)."""
    from sqlalchemy import select

    from database.models import NflGame
    from services.game_features import conditions_for, team_profiles
    from services.projection_factors import factor, inputs_for

    cache = data.cache
    if ("games",) not in cache:
        games = {}
        for g in session.scalars(select(NflGame).where(NflGame.season == data.season, NflGame.game_type == "REG")):
            games[(g.week, g.home_team)] = games[(g.week, g.away_team)] = g
        cache[("games",)] = games
    if ("profiles", week) not in cache:
        cache[("profiles", week)] = team_profiles(session, data.season, week)
    game = cache[("games",)].get((week, team))
    return factor(position, inputs_for(cache[("profiles", week)], opponent, conditions_for(game)))


def score(targets: list[Target]) -> dict:
    actual = np.array([t.actual for t in targets])
    projected = np.array([t.projected for t in targets])
    by_week_pos: dict[tuple, list[Target]] = defaultdict(list)
    for t in targets:
        by_week_pos[(t.week, t.position)].append(t)
    rhos = []
    for group in by_week_pos.values():
        if len(group) >= 8:
            a = np.argsort(np.argsort([t.actual for t in group]))
            p = np.argsort(np.argsort([t.projected for t in group]))
            rhos.append(np.corrcoef(a, p)[0, 1])
    return {"n": len(targets), "mae": float(np.mean(np.abs(actual - projected))),
            "rmse": float(np.sqrt(np.mean((actual - projected) ** 2))), "rank_corr": float(np.nanmean(rhos))}


def run(session: Session, data: SeasonData, settings: WeeklyModelSettings) -> list[Target]:
    targets: list[Target] = []
    for week in range(FIRST_WEEK, LAST_WEEK + 1):
        targets.extend(project_week(session, data, week, settings))
    return targets


# Projection tiers (if-active points): stars have tighter relative floors than fringe players.
TIERS = ((0.0, 8.0), (8.0, 12.0), (12.0, 16.0), (16.0, 999.0))
FULL_ROLE_SNAPS = 50.0          # games below this snap share are usually injury exits or benchings
MIN_TIER_GAMES = 40


def _quantiles(ratios: list[float]) -> dict[str, float]:
    q10, q50, q90 = np.percentile(ratios, [10, 50, 90])
    return {"floor": round(float(q10), 3), "median": round(float(q50), 3), "ceiling": round(float(q90), 3),
            "bust_rate": round(mean(1.0 if r < 0.5 else 0.0 for r in ratios), 3), "n": len(ratios)}


def ranges(targets: list[Target]) -> dict[str, dict]:
    """Floor/ceiling multipliers by position and projection tier.

    The 10th/90th percentile of actual / if-active projection, over games the player played
    in a real role (snap share >= FULL_ROLE_SNAPS, or unknown). The floor answers
    "if he plays his normal role"; an in-game injury is a separate risk.
    """
    out: dict[str, dict] = {}
    for position in ("QB", "RB", "WR", "TE"):
        games = [t for t in targets if t.position == position and t.if_active > 0 and t.played
                 and (t.snap_pct is None or t.snap_pct >= FULL_ROLE_SNAPS)]
        if len(games) < 50:
            continue
        overall = _quantiles([t.actual / t.if_active for t in games])
        tiers = []
        for low, high in TIERS:
            ratios = [t.actual / t.if_active for t in games if low <= t.if_active < high]
            if len(ratios) >= MIN_TIER_GAMES:
                tiers.append({"min": low, "max": high, **_quantiles(ratios)})
        out[position] = {**overall, "tiers": tiers}
    return out


GRID = {"half_life": [None, 2.0, 3.0, 4.0, 6.0], "sos_weight": [0.0, 0.5, 1.0, 1.5],
        "implied_weight": [0.0, 0.5, 1.0], "use_injuries": [False, True]}


def main(argv: list[str] | None = None) -> None:
    from database.db import engine

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, default=2025)
    parser.add_argument("--quick", action="store_true", help="only evaluate the production settings")
    parser.add_argument("--no-write", action="store_true", help="don't update projection_ranges.json")
    parser.add_argument("--ablation", action="store_true", help="production settings with one piece removed at a time")
    args = parser.parse_args(argv)
    with Session(engine) as session:
        production = WeeklyModelSettings()
        if args.ablation:
            candidates = [production, replace(production, use_factors=False), replace(production, use_injuries=False),
                          replace(production, half_life=None),
                          replace(production, implied_weight=0.0), replace(production, sos_weight=0.0),
                          WeeklyModelSettings(half_life=None, sos_weight=0.0, implied_weight=0.0, use_injuries=False)]
        else:
            candidates = [production] if args.quick else [
                WeeklyModelSettings(**dict(zip(GRID, values))) for values in itertools.product(*GRID.values())]
        results = []
        data = load_season(session, args.season)
        for settings in candidates:
            targets = run(session, data, settings)
            results.append((score(targets), settings, targets))
        results.sort(key=lambda r: r[0]["mae"])
        log.info("%-58s %6s %6s %6s %6s", "settings", "n", "MAE", "RMSE", "rank r")
        for metrics, settings, _ in results[:12]:
            log.info("%-58s %6d %6.3f %6.3f %6.3f", settings.describe(), metrics["n"], metrics["mae"],
                     metrics["rmse"], metrics["rank_corr"])
        baseline = next((r for r in results if r[1] == WeeklyModelSettings(half_life=None, sos_weight=0.0,
                                                                          implied_weight=0.0, use_injuries=False)), None)
        if baseline:
            log.info("baseline (season rate only): MAE %.3f, rank r %.3f", baseline[0]["mae"], baseline[0]["rank_corr"])
        if args.ablation or args.quick:
            results.sort(key=lambda r: r[1] != production)        # write ranges for the production settings
        best_metrics, best, best_targets = results[0]
        log.info("best: %s", best.describe())
        computed = ranges(best_targets)
        log.info("ranges: %s", computed)
        if not args.no_write:
            RANGES_PATH.write_text(json.dumps({"season": args.season, "settings": best.describe(),
                                               "metrics": best_metrics, "positions": computed}, indent=2) + "\n")
            log.info("wrote %s", RANGES_PATH)


if __name__ == "__main__":
    main()
