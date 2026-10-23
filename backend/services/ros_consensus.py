"""Rest-of-season consensus: blend the model with expert ROS ranks and this week's projections.

    ros_ppg = 0.45 x model ROS (rate shrunk to last season, times remaining schedule)
            + 0.35 x Yahoo ROS experts (consensus and Josh & Hayden ranks, as points)
            + 0.20 x this week's outside projections (ESPN and Vegas props)

Weights re-balance over the inputs a player has. Expert ranks become points through the
position's model curve: ROS rank N gets the Nth best model ROS projection.

Availability: a player on IR, PUP or suspended (Sleeper) is assumed to miss
IR_WEEKS more games; one ruled Out or Doubtful this week misses one. His rest-of-season
points per game are scaled by the share of remaining games he's expected to play.

The model part is backtested (services/backtest.py). There's no stored history of
expert ROS ranks to backtest the blend; it leans on the market because a single model is
slow to react to role changes the experts already see.
"""
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import PlayerStatus, WeeklySourceProjection
from services.scoring import ScoringSettings

WEIGHTS = {"model": 0.45, "experts": 0.35, "weekly": 0.20}
ROS_SOURCE = "yahoo_ros"
WEEKLY_SOURCE_POINTS = ("espn", "vegas_props")
PRIMARY_STAT = {"QB": "pass_yds", "RB": "rush_yds", "WR": "rec_yds", "TE": "rec_yds"}
IR_STATUSES = {"IR", "PUP", "SUS", "NFI", "IR-R"}
IR_WEEKS = 4
SHORT_OUT = {"OUT", "DOUBTFUL"}
CONSENSUS, JOSH_AND_HAYDEN = "consensus", ("1408", "7666")


@dataclass(frozen=True)
class RosInputs:
    model: float
    experts: float | None = None
    expert_rank: float | None = None
    weekly: float | None = None
    availability: float = 1.0          # share of remaining games expected to play

    def blended(self) -> float:
        parts = {"model": self.model, "experts": self.experts, "weekly": self.weekly}
        parts = {k: v for k, v in parts.items() if v is not None}
        total = sum(WEIGHTS[k] for k in parts)
        return round(sum(WEIGHTS[k] * v for k, v in parts.items()) / total * self.availability, 2)


def _latest_week(session: Session, season: int, source: str) -> int | None:
    return session.scalar(select(func.max(WeeklySourceProjection.week)).where(
        WeeklySourceProjection.season == season, WeeklySourceProjection.source == source))


def _rank_points(curve: Sequence[float], rank: float) -> float:
    index = min(max(rank - 1, 0.0), len(curve) - 1)
    low = int(index)
    high = min(low + 1, len(curve) - 1)
    return curve[low] + (curve[high] - curve[low]) * (index - low)


def expert_ranks(session: Session, season: int, scoring: str) -> dict[int, float]:
    """{player_id: ROS positional rank}: average of consensus and Josh & Hayden where present."""
    week = _latest_week(session, season, ROS_SOURCE)
    if week is None:
        return {}
    rows = session.execute(select(WeeklySourceProjection.player_id, WeeklySourceProjection.expert,
                                  WeeklySourceProjection.rank).where(
        WeeklySourceProjection.season == season, WeeklySourceProjection.week == week,
        WeeklySourceProjection.source == ROS_SOURCE, WeeklySourceProjection.scoring == scoring,
        WeeklySourceProjection.player_id.is_not(None))).all()
    by_player: dict[int, dict[str, float]] = defaultdict(dict)
    for player_id, expert, rank in rows:
        if rank is not None:
            by_player[player_id][expert] = rank
    ranks = {}
    for player_id, experts in by_player.items():
        josh_hayden = [experts[e] for e in JOSH_AND_HAYDEN if e in experts]
        parts = [experts[CONSENSUS]] if CONSENSUS in experts else []
        if josh_hayden:
            parts.append(sum(josh_hayden) / len(josh_hayden))
        if parts:
            ranks[player_id] = sum(parts) / len(parts)
    return ranks


def weekly_points(session: Session, season: int, position_of: Mapping[int, str],
                  scoring: ScoringSettings) -> dict[int, float]:
    """{player_id: this week's ESPN/props projection}, averaged across the two."""
    from services.weekly_model import points_from_stats

    week = _latest_week(session, season, "espn")
    if week is None:
        return {}
    rows = session.execute(select(WeeklySourceProjection.player_id, WeeklySourceProjection.source,
                                  WeeklySourceProjection.stats).where(
        WeeklySourceProjection.season == season, WeeklySourceProjection.week == week,
        WeeklySourceProjection.source.in_(WEEKLY_SOURCE_POINTS),
        WeeklySourceProjection.player_id.is_not(None))).all()
    points: dict[int, list[float]] = defaultdict(list)
    for player_id, source, stats in rows:
        position = position_of.get(player_id)
        parsed = json.loads(stats or "{}")
        if not position or not parsed or (source == "vegas_props" and PRIMARY_STAT[position] not in parsed):
            continue
        points[player_id].append(points_from_stats(parsed, position, scoring))
    return {pid: sum(v) / len(v) for pid, v in points.items()}


def availability(session: Session, remaining_weeks: int) -> dict[int, float]:
    """{player_id: share of remaining games expected to play} from live status."""
    out = {}
    for status in session.scalars(select(PlayerStatus).where(PlayerStatus.injury_status.is_not(None))):
        tag = (status.injury_status or "").upper()
        missed = IR_WEEKS if tag in IR_STATUSES else 1 if tag in SHORT_OUT else 0
        if missed:
            out[status.player_id] = max(0.0, (remaining_weeks - missed) / max(remaining_weeks, 1))
    return out


def ros_inputs(session: Session, season: int, scoring_key: str, scoring: ScoringSettings,
               model_ppg: Mapping[int, float], positions: Mapping[int, str],
               remaining_weeks: int) -> dict[int, RosInputs]:
    ranks = expert_ranks(session, season, scoring_key)
    weekly = weekly_points(session, season, positions, scoring)
    avail = availability(session, remaining_weeks)
    curves: dict[str, list[float]] = defaultdict(list)
    for pid, ppg in model_ppg.items():
        curves[positions.get(pid, "")].append(ppg)
    for curve in curves.values():
        curve.sort(reverse=True)
    out = {}
    for pid, ppg in model_ppg.items():
        rank = ranks.get(pid)
        curve = curves[positions.get(pid, "")]
        out[pid] = RosInputs(model=ppg, experts=round(_rank_points(curve, rank), 2) if rank and curve else None,
                             expert_rank=rank, weekly=weekly.get(pid), availability=avail.get(pid, 1.0))
    return out


def data_version(session: Session, season: int) -> tuple:
    """Changes when expert ROS ranks, weekly sources or live status are refreshed."""
    return (session.scalar(select(func.max(WeeklySourceProjection.fetched_at)).where(
                WeeklySourceProjection.season == season)),
            session.scalar(select(func.max(PlayerStatus.fetched_at))))
