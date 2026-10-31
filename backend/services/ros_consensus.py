"""Rest-of-season consensus: blend the model with expert ROS ranks and this week's projections.

    ros_ppg = 0.45 x model ROS (rate shrunk to last season, times remaining schedule)
            + 0.35 x Yahoo ROS experts (consensus and Josh & Hayden ranks, as points)
            + 0.20 x this week's outside projections (ESPN and Vegas props)

Weights re-balance over the inputs a player has. Expert ranks become points through the
position's model curve: ROS rank N gets the Nth best model ROS projection.

Availability: the valuation passes each injured player's share of remaining value from
services/injury_recovery.py (games expected to miss by body part and practice reports, plus
the slower games after a return). Without it (status_shares, the fallback), a player on IR,
PUP or suspended misses IR_WEEKS more games and one ruled Out or Doubtful misses one. A
season-ending injury (IR with a torn ACL or Achilles, or notes saying so) misses every
remaining game, so he has no rest-of-season value. His rest-of-season points per game are
scaled by that share.

The model part is backtested (services/backtest.py). There's no stored history of
expert ROS ranks to backtest the blend; it leans on the market because a single model is
slow to react to role changes the experts already see.
"""
import json
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
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
SEASON_ENDING = ("ACL", "ACHILLES", "SEASON")       # in the IR body part / notes
CONSENSUS, JOSH_AND_HAYDEN = "consensus", ("1408", "7666")
ROS_LOOKBACK_WEEKS = 2
# Yahoo publishes formats at different times; positional ranks barely differ between them.
SCORING_FALLBACK = {"ppr": ("ppr", "half", "std"), "half": ("half", "ppr", "std"), "std": ("std", "half", "ppr")}


@dataclass(frozen=True)
class RosInputs:
    model: float
    experts: float | None = None
    expert_rank: float | None = None
    weekly: float | None = None
    availability: float = 1.0          # share of remaining games expected to play
    role_share: float = 1.0            # a backup QB's share (the games his starter misses), apart from injury

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
    """{player_id: ROS positional rank}: average of consensus and Josh & Hayden where present.

    Each player uses his most recent week within ROS_LOOKBACK_WEEKS: Yahoo publishes a new
    week's ranks gradually, and a half-published week mustn't erase everyone else's rank.
    """
    week = _latest_week(session, season, ROS_SOURCE)
    if week is None:
        return {}
    rows = session.execute(select(WeeklySourceProjection.player_id, WeeklySourceProjection.week,
                                  WeeklySourceProjection.scoring, WeeklySourceProjection.expert,
                                  WeeklySourceProjection.rank).where(
        WeeklySourceProjection.season == season, WeeklySourceProjection.week > week - ROS_LOOKBACK_WEEKS,
        WeeklySourceProjection.source == ROS_SOURCE, WeeklySourceProjection.player_id.is_not(None))).all()
    # Newest week first; within a week, the league's scoring, then the nearest other format.
    preference = SCORING_FALLBACK.get(scoring, (scoring,))
    by_key: dict[int, dict[tuple[int, int], dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    for player_id, row_week, row_scoring, expert, rank in rows:
        if rank is not None and row_scoring in preference:
            by_key[player_id][(row_week, -preference.index(row_scoring))][expert] = rank
    by_player = {pid: keyed[max(keyed)] for pid, keyed in by_key.items()}
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
                  scoring: ScoringSettings) -> tuple[int | None, dict[int, float]]:
    """(week, {player_id: that week's ESPN/props projection}), averaged across the two."""
    from services.weekly_model import points_from_stats

    week = _latest_week(session, season, "espn")
    if week is None:
        return None, {}
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
    return week, {pid: sum(v) / len(v) for pid, v in points.items()}


def season_ending(tag: str, body_part: str | None) -> bool:
    return tag in IR_STATUSES and any(word in (body_part or "").upper() for word in SEASON_ENDING)


def status_shares(session: Session, remaining_weeks: int) -> dict[int, float]:
    """{player_id: share of remaining games expected to play} for every player with a live status."""
    out = {}
    for status in session.scalars(select(PlayerStatus).where(PlayerStatus.injury_status.is_not(None))):
        tag = (status.injury_status or "").upper()
        if season_ending(tag, status.injury_body_part):
            out[status.player_id] = 0.0
            continue
        missed = IR_WEEKS if tag in IR_STATUSES else 1 if tag in SHORT_OUT else 0
        out[status.player_id] = max(0.0, (remaining_weeks - missed) / max(remaining_weeks, 1))
    return out


BACKUP_ROS_FLOOR = 0.03       # a backup QB still starts a game or two over a season, on average


def backup_qb_shares(session: Session, shares: Mapping[int, float],
                     starters: Mapping[str, int] | None = None) -> dict[int, float]:
    """Share of remaining games a backup QB starts: the share his starter misses (nearly 0 when healthy).

    starters: {team: the QB who started most of this season}. When he's hurt the depth chart lists
    his fill-in first; he stays the starter (and the fill-in the backup) instead of dropping to 0.
    """
    from services.injuries import qb_depth

    out = {}
    for team, qbs in qb_depth(session).items():
        starter = (starters or {}).get(team)
        if starter in qbs[1:] and shares.get(starter, 1.0) < 1:
            qbs = [starter, *(q for q in qbs if q != starter)]
        for backup in qbs[1:]:
            out[backup] = round(max(BACKUP_ROS_FLOOR, 1.0 - shares.get(qbs[0], 1.0)), 3)
    return out


def availability(session: Session, remaining_weeks: int) -> dict[int, float]:
    """{player_id: share of remaining games expected to play}, for players expected to miss some.

    Backup QBs count as missing every game their starter plays.
    """
    return _availability(session, remaining_weeks)[0]


def _availability(session: Session, remaining_weeks: int, injury_shares: Mapping[int, float] | None = None,
                  qb_starters: Mapping[str, int] | None = None) -> tuple[dict[int, float], dict[int, float]]:
    """(availability as above, {backup QB: share of games his starter misses}).

    injury_shares replaces status_shares (services.injury_recovery.shares)."""
    shares = dict(injury_shares) if injury_shares is not None else status_shares(session, remaining_weeks)
    backups = backup_qb_shares(session, shares, qb_starters)
    for pid, backup in backups.items():
        shares[pid] = min(shares.get(pid, 1.0), backup)
    return {pid: share for pid, share in shares.items() if share < 1}, backups


def ros_inputs(session: Session, season: int, scoring_key: str, scoring: ScoringSettings,
               model_ppg: Mapping[int, float], positions: Mapping[int, str],
               remaining_weeks: int,
               matchup_for: Callable[[int], Mapping[int, float]] | None = None,
               injury_shares: Mapping[int, float] | None = None,
               qb_starters: Mapping[str, int] | None = None) -> dict[int, RosInputs]:
    """matchup_for(week): each player's matchup factor that week. The weekly piece is divided by the
    factor for the week it projects, so one easy or hard matchup doesn't move rest-of-season value."""
    ranks = expert_ranks(session, season, scoring_key)
    week, weekly = weekly_points(session, season, positions, scoring)
    matchup = matchup_for(week) if matchup_for and week is not None else {}
    avail, backups = _availability(session, remaining_weeks, injury_shares, qb_starters)
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
                             expert_rank=rank, availability=avail.get(pid, 1.0), role_share=backups.get(pid, 1.0),
                             weekly=weekly[pid] / matchup.get(pid, 1.0) if pid in weekly else None)
    return out


def data_version(session: Session, season: int) -> tuple:
    """Changes when expert ROS ranks, weekly sources, live status or injury news are refreshed."""
    from database.models import InjuryNews

    return (session.scalar(select(func.max(WeeklySourceProjection.fetched_at)).where(
                WeeklySourceProjection.season == season)),
            session.scalar(select(func.max(PlayerStatus.fetched_at))),
            session.scalar(select(func.max(InjuryNews.checked_at))))
