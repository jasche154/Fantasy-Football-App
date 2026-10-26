"""Rest-of-season player values: projection -> value over replacement -> trade value.

trade_value = max(0, ros_ppg - replacement_ppg) * weeks_remaining, i.e. the rest-of-season
fantasy points a player is expected to score above the best waiver-wire option.

ros_ppg includes strength of schedule: the player's rate is scaled by the average
opponent-adjusted factor of the defenses left on their schedule (services.schedule_strength).
"""
from collections.abc import Sequence
from dataclasses import dataclass, field
from threading import Lock
from types import MappingProxyType
from typing import Mapping

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config import get_settings
from database.models import DataLoad, PlayerStatsWeekly
from services.league import LeagueSettings, replacement_levels
from services.projections import (
    MIN_PRIOR_GAMES, PlayerSeason, current_rate, latest_season, latest_week,
    load_player_seasons, project_ros_ppg,
)
from services.injuries import ros_role_losses
from services.ros_consensus import RosInputs, ros_inputs, status_shares
from services.ros_consensus import data_version as ros_data_version
from services.schedule_strength import NEUTRAL, DefenseStrength, Schedule, defense_strength, load_schedule

# Exponent on the remaining-schedule factor; 1.0 applies the matchup difference in full.
ROS_SCHEDULE_WEIGHT = 1.0


@dataclass(frozen=True)
class PlayerValue:
    player_id: int
    name: str
    position: str | None
    team: str | None
    games: int
    ppg: float
    expected_ppg: float | None
    ros_ppg: float
    vorp_per_game: float
    trade_value: float
    rank: int = 0
    pos_rank: str = ""
    base_ros_ppg: float = 0.0          # before the schedule adjustment
    schedule_factor: float = 1.0       # >1 = easier remaining schedule
    playoff_factor: float = 1.0        # >1 = easier fantasy-playoff schedule (the last PLAYOFF_LENGTH weeks)
    is_rookie: bool = False                # no games last season (rookie, or back from a lost year)
    returning_starter: str | None = None   # a starter whose return shrinks this fill-in's role
    returning_loss: float = 0.0            # ppg taken off the model rate for that
    model_ros_ppg: float = 0.0         # the model alone (with schedule), before the consensus blend
    expert_ros_rank: float | None = None
    expert_ros_ppg: float | None = None
    weekly_ppg: float | None = None
    availability: float = 1.0


@dataclass(frozen=True)
class Valuation:
    season: int
    through_week: int | None
    weeks_remaining: int
    league: LeagueSettings
    replacement: Mapping[str, float]
    values: tuple[PlayerValue, ...]
    seasons: tuple[PlayerSeason, ...] = ()
    by_id: Mapping[int, PlayerValue] = field(default_factory=dict)
    schedule: Schedule = field(default_factory=lambda: Schedule(games=()))
    defense: DefenseStrength = NEUTRAL
    priors: Mapping[int, float] = field(default_factory=dict)      # last season's PPG (4+ games)


def weeks_remaining(through_week: int | None, end_week: int) -> int:
    return max(1, end_week - (through_week or 0))


def _priors(previous: Sequence[PlayerSeason]) -> dict[int, float]:
    return {p.player_id: p.ppg for p in previous if p.games_played >= MIN_PRIOR_GAMES}


def _rank(values: list[PlayerValue]) -> tuple[PlayerValue, ...]:
    ordered = sorted(values, key=lambda v: (-v.trade_value, -v.ros_ppg, v.name))
    position_counts: dict[str, int] = {}
    ranked = []
    for overall, value in enumerate(ordered, start=1):
        position = value.position or "?"
        position_counts[position] = position_counts.get(position, 0) + 1
        ranked.append(PlayerValue(**{**value.__dict__, "rank": overall,
                                     "pos_rank": f"{position}{position_counts[position]}"}))
    return tuple(ranked)


def value_players(current: Sequence[PlayerSeason], previous: Sequence[PlayerSeason],
                  league: LeagueSettings, remaining: int,
                  schedule_factors: Mapping[int, float] | None = None,
                  consensus: Mapping[int, "RosInputs"] | None = None,
                  playoff_factors: Mapping[int, float] | None = None,
                  role_losses: Mapping[int, tuple[str, float]] | None = None,
                  ) -> tuple[tuple[PlayerValue, ...], dict[str, float]]:
    """Pure valuation step, separated from data loading so it can be tested directly."""
    rates = {p.player_id: current_rate(p) for p in current}
    first_pass = replacement_levels(((p.position, rates[p.player_id]) for p in current), league)
    priors = _priors(previous)
    base = {p.player_id: project_ros_ppg(rates[p.player_id], p.games_played,
                                         priors.get(p.player_id, first_pass.get(p.position, 0.0)))
            for p in current}
    losses = role_losses or {}
    rookies = {p.player_id for p in current} - {p.player_id for p in previous}
    base = {pid: round(max(0.0, ppg - losses.get(pid, ("", 0.0))[1]) * (ROOKIE_GROWTH if pid in rookies else 1.0), 2)
            for pid, ppg in base.items()}
    factors = {pid: (schedule_factors or {}).get(pid, 1.0) ** ROS_SCHEDULE_WEIGHT for pid in base}
    model = {pid: round(base[pid] * factors[pid], 2) for pid in base}
    consensus = consensus or {}
    projected = {pid: consensus[pid].blended() if pid in consensus else model[pid] for pid in base}
    replacement = replacement_levels(((p.position, projected[p.player_id]) for p in current), league)

    values = []
    for p in current:
        vorp = round(max(0.0, projected[p.player_id] - replacement.get(p.position, 0.0)), 2)
        values.append(PlayerValue(
            player_id=p.player_id, name=p.name, position=p.position, team=p.team,
            games=p.games_played, ppg=p.ppg, expected_ppg=p.expected_ppg,
            ros_ppg=projected[p.player_id], vorp_per_game=vorp,
            trade_value=round(vorp * remaining, 1), base_ros_ppg=base[p.player_id],
            schedule_factor=round(factors[p.player_id], 3), model_ros_ppg=model[p.player_id],
            playoff_factor=round((playoff_factors or {}).get(p.player_id, 1.0), 3),
            is_rookie=p.player_id in rookies,
            returning_starter=losses.get(p.player_id, (None, 0.0))[0],
            returning_loss=losses.get(p.player_id, ("", 0.0))[1],
            **_consensus_fields(consensus.get(p.player_id)),
        ))
    return _rank(values), replacement


def _consensus_fields(inputs: "RosInputs | None") -> dict:
    if inputs is None:
        return {}
    return {"expert_ros_rank": inputs.expert_rank, "expert_ros_ppg": inputs.experts,
            "weekly_ppg": None if inputs.weekly is None else round(inputs.weekly, 2),
            "availability": round(inputs.availability, 3)}


PLAYOFF_LENGTH = 3      # most leagues: weeks 15-17
# Rookies keep more of their first-half scoring in the second half: 2023-25 RB/WR/TE with 4+
# games in each half, median second/first-half ratio 0.952 for rookies (n=54) vs 0.922 for
# veterans (n=389), about 3% better. Applied to the model's rest-of-season rate.
ROOKIE_GROWTH = 1.03


def playoff_weeks(end_week: int) -> tuple[int, int]:
    """The fantasy playoffs: the last PLAYOFF_LENGTH weeks of the fantasy season."""
    return end_week - PLAYOFF_LENGTH + 1, end_week


def _compute(session: Session, league: LeagueSettings, season: int) -> Valuation:
    scoring = league.scoring_settings()
    current = load_player_seasons(session, season, scoring)
    previous = load_player_seasons(session, season - 1, scoring)
    through = latest_week(session, season)
    end_week = get_settings().fantasy_end_week
    remaining = weeks_remaining(through, end_week)
    schedule = load_schedule(session, season)
    defense = defense_strength(current, schedule)
    factors = {p.player_id: defense.schedule_factor(
        schedule.remaining_opponents(p.team, through or 0, end_week), p.position) for p in current}
    playoffs = playoff_weeks(end_week)
    playoff = {p.player_id: defense.schedule_factor(
        schedule.remaining_opponents(p.team, max(playoffs[0] - 1, through or 0), playoffs[1]), p.position)
        for p in current}
    losses = ros_role_losses(current, (through or 0) + 1, schedule, status_shares(session, remaining))
    model_values, _ = value_players(current, previous, league, remaining, factors, role_losses=losses)
    consensus = ros_inputs(session, season, league.scoring, scoring,
                           {v.player_id: v.model_ros_ppg for v in model_values},
                           {v.player_id: v.position or "" for v in model_values}, remaining)
    values, replacement = value_players(current, previous, league, remaining, factors, consensus, playoff, losses)
    return Valuation(season=season, through_week=through, weeks_remaining=remaining, league=league,
                     replacement=MappingProxyType(replacement), values=values,
                     seasons=tuple(current), by_id=MappingProxyType({v.player_id: v for v in values}),
                     schedule=schedule, defense=defense, priors=MappingProxyType(_priors(previous)))


_cache: dict[tuple, Valuation] = {}
_cache_lock = Lock()
CACHE_SIZE = 32


class NoDataError(LookupError):
    """The requested season has no regular-season stats loaded."""


def _data_version(session: Session, season: int) -> tuple:
    """Changes whenever data is reloaded, which invalidates cached values.

    The populate_stats load stamp catches reloads that keep row counts and ids identical
    (SQLite reuses rowids); the stats count/max id catches rows written any other way.
    """
    stats = session.execute(select(func.count(PlayerStatsWeekly.id), func.max(PlayerStatsWeekly.id))
                            .where(PlayerStatsWeekly.season == season)).one()
    return (*stats, session.scalar(select(func.max(DataLoad.id))), *ros_data_version(session, season))


def build_valuation(session: Session, league: LeagueSettings, season: int | None = None) -> Valuation:
    season = season or latest_season(session)
    if season is None:
        raise NoDataError("No player stats loaded. Run `python -m database.populate_stats` first.")
    if latest_week(session, season) is None:
        raise NoDataError(f"No regular-season stats loaded for {season}.")
    key = (str(session.get_bind().url), season, league, _data_version(session, season))
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached
    valuation = _compute(session, league, season)
    with _cache_lock:
        if len(_cache) >= CACHE_SIZE:
            _cache.pop(next(iter(_cache)))
        _cache[key] = valuation
    return valuation
