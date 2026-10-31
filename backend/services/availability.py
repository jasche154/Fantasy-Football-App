"""Chance to play: injury reports, live tags, untagged sit risk and backup quarterbacks.

Layers, newest first:
1. Sleeper live status (player_status, refreshed hourly): game-day Out/Doubtful/Questionable,
   IR, PUP, suspensions.
2. The official weekly injury report (injury_reports, nflverse): game status plus the final
   practice participation (DNP / LP / FP).

**Play probability.** Each status and practice combination maps to a learned chance of playing:
how often a player who recorded stats the week before recorded stats again with that tag
(2021 onward), relative to a healthy full-practice player. That gives roughly: Questionable+DNP
about 0.5, Questionable+LP about 0.77, Questionable+FP about 0.84, Doubtful about 0.03, Out 0.

**Untagged players** still sit sometimes, by last week's snap share; **backup quarterbacks** only
play when the QBs ahead of them sit. Vacated opportunity lives in services/injuries.py.
"""
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import DataLoad, DepthChartEntry, InjuryReport, PlayerStatsWeekly, PlayerStatus
from integrations.weekly_sources import utc
from services.projections import PlayerSeason
from services.schedule_strength import Schedule

SLEEPER_FRESH_HOURS = 18
SLEEPER_ZERO = {"OUT", "IR", "PUP", "SUS", "DNR", "COV", "NA", "IR-R", "NFI"}
MIN_RATE_SAMPLE = 15
REST_DAY = "not injury related"
LAST_REGULAR_WEEK = 18
# How a position's vacated work is redistributed (the rest is lost: efficiency, other positions).
HEALTHY = ("NONE", "FP")
DEFAULT_RATES = {("OUT", "*"): 0.0, ("DOUBTFUL", "*"): 0.03, ("QUESTIONABLE", "DNP"): 0.49,
                 ("QUESTIONABLE", "LP"): 0.77, ("QUESTIONABLE", "FP"): 0.84, ("QUESTIONABLE", "*"): 0.75,
                 ("NONE", "DNP"): 0.8, ("NONE", "LP"): 0.98, ("NONE", "FP"): 1.0}


@dataclass(frozen=True)
class Availability:
    player_id: int
    play_prob: float
    status: str | None          # "Questionable", "Out", "IR", ...
    practice: str | None        # final practice that week: DNP / LP / FP
    injury: str | None
    source: str                 # "sleeper", "report", "none"

    @property
    def label(self) -> str:
        if self.status is None and self.practice in (None, "FP"):
            return "Healthy"
        practice = f", {self.practice} in practice" if self.practice else ""
        injury = f" ({self.injury})" if self.injury else ""
        return f"{self.status or 'No designation'}{injury}{practice}"


HEALTHY_AVAILABILITY = Availability(0, 1.0, None, None, None, "none")


HEALTHY_AVAILABILITY = Availability(0, 1.0, None, None, None, "none")


_rate_cache: dict[tuple, dict[tuple[str, str], float]] = {}
_rate_lock = Lock()


def _key(status: str | None, practice: str | None) -> tuple[str, str]:
    return ((status or "NONE").upper(), (practice or "*").upper())


def learned_play_rates(session: Session, exclude_season: int | None = None) -> dict[tuple[str, str], float]:
    """{(status, practice): chance of playing}, learned from regular-season reports.

    exclude_season leaves one season out (the backtest must not learn from the games it tests).
    """
    version = (str(session.get_bind().url), session.scalar(select(func.max(DataLoad.id))), exclude_season)
    with _rate_lock:
        if version in _rate_cache:
            return _rate_cache[version]
    # A set lookup in Python: the equivalent SQL self-join takes over a minute on SQLite.
    played = set(session.execute(select(PlayerStatsWeekly.player_id, PlayerStatsWeekly.season, PlayerStatsWeekly.week)
                                 .where(PlayerStatsWeekly.season_type == "REG")).tuples())
    reports = session.execute(select(InjuryReport.player_id, InjuryReport.season, InjuryReport.week,
                                     InjuryReport.report_status, InjuryReport.practice_status)
                              .where(InjuryReport.week <= LAST_REGULAR_WEEK,
                                     InjuryReport.season != (exclude_season or -1))).all()
    tallies: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for player_id, season, week, status, practice in reports:
        if (player_id, season, week - 1) not in played:
            continue                                   # only players who contributed the week before
        tally = tallies[_key(status, practice)]
        tally[0] += 1
        tally[1] += (player_id, season, week) in played
    observed = {key: (n, hits / n) for key, (n, hits) in tallies.items() if n >= MIN_RATE_SAMPLE}
    healthy = observed.get(HEALTHY, (0, None))[1]
    rates = dict(DEFAULT_RATES)
    if healthy:
        for key, (_, rate) in observed.items():
            rates[key] = round(min(1.0, rate / healthy), 3)
        rates[("OUT", "*")] = 0.0
    with _rate_lock:
        _rate_cache[version] = rates
    return rates


def play_probability(rates: Mapping[tuple[str, str], float], status: str | None, practice: str | None) -> float:
    status_key = (status or "NONE").upper()
    if status_key in SLEEPER_ZERO:
        return 0.0
    for key in ((status_key, (practice or "*").upper()), (status_key, "*")):
        if key in rates:
            return rates[key]
    return 1.0 if status_key == "NONE" else rates.get(("QUESTIONABLE", "*"), 0.75)


def load_availability(session: Session, season: int, week: int, use_live: bool = True,
                      exclude_rate_season: int | None = None) -> dict[int, Availability]:
    """Every listed player's availability for the week (unlisted players are healthy).

    use_live adds Sleeper's current tags; only valid for the current week.
    """
    rates = learned_play_rates(session, exclude_rate_season)
    report = {r.player_id: r for r in session.scalars(select(InjuryReport).where(
        InjuryReport.season == season, InjuryReport.week == week))}
    live = {}
    if use_live:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=SLEEPER_FRESH_HOURS)
        live = {s.player_id: s for s in session.scalars(select(PlayerStatus)) if utc(s.fetched_at) >= cutoff}
    availability = {}
    for player_id in set(report) | {pid for pid, s in live.items() if s.injury_status}:
        r, s = report.get(player_id), live.get(player_id)
        practice = r.practice_status if r else None
        if s is not None and s.injury_status:
            status, source, injury = s.injury_status, "sleeper", s.injury_body_part or (r.injury if r else None)
        else:
            status, source, injury = (r.report_status if r else None), "report", (r.injury if r else None)
        if status is None and injury and REST_DAY in injury.lower():
            practice = None                          # veteran rest days aren't injuries
        availability[player_id] = Availability(player_id, play_probability(rates, status, practice),
                                               status, practice, injury, source)
    return availability


def team_weeks(schedule: Schedule, team: str, before_week: int) -> list[int]:
    return [g.week for g in schedule.games
            if g.is_final and g.week < before_week and team in (g.home_team, g.away_team)]


BACKUP_FLOOR = 0.02            # a healthy starter can still get hurt mid-game

# Untagged players still sit sometimes: the chance by position and last week's offensive snap
# share (upper bounds 10/25/50/75/100%), measured 2021-2026. Applied when it's 10% or more.
SNAP_EDGES = (10.0, 25.0, 50.0, 75.0, 101.0)
SIT_RISK = {"RB": (0.24, 0.11, 0.05, 0.018, 0.017), "WR": (0.20, 0.12, 0.07, 0.04, 0.018),
            "TE": (0.21, 0.10, 0.04, 0.023, 0.023), "QB": (0.64, 0.74, 0.48, 0.32, 0.05)}
INACTIVE_LAST_WEEK_PLAYS = 0.34
MIN_SIT_RISK = 0.10


def usage_availability(seasons: Iterable[PlayerSeason], availability: Mapping[int, Availability],
                       week: int, schedule: Schedule) -> dict[int, Availability]:
    """Untagged players who barely played, or sat, in their team's last game.

    "Inactive" needs evidence: the team's box score for that week is loaded (a teammate has a
    line) and his own most recent game was for the same team (not a trade with a bye between).
    """
    seasons = list(seasons)
    loaded = {(schedule.opponent(g.opponent, g.week), g.week) for p in seasons for g in p.games if g.opponent}
    out = {}
    for player in seasons:
        if player.player_id in availability or not player.team or player.position not in SIT_RISK or not player.games:
            continue
        played = team_weeks(schedule, player.team, week)
        if not played:
            continue
        last = next((g for g in player.games if g.week == played[-1]), None)
        recent = player.games[-1]
        same_team = recent.opponent is not None and schedule.opponent(recent.opponent, recent.week) == player.team
        if last is None and (player.team, played[-1]) in loaded and same_team:
            out[player.player_id] = Availability(player.player_id, INACTIVE_LAST_WEEK_PLAYS, "Check inactives",
                                                 None, "inactive last week", "usage")
            continue
        if last is None or last.snap_pct is None:
            continue
        band = next((i for i, edge in enumerate(SNAP_EDGES) if last.snap_pct < edge), len(SNAP_EDGES) - 1)
        risk = SIT_RISK[player.position][band]
        if risk >= MIN_SIT_RISK:
            out[player.player_id] = Availability(player.player_id, round(1 - risk, 3), "Low snaps", None,
                                                 f"{last.snap_pct:.0f}% of snaps last week", "usage")
    return out


def qb_depth(session: Session) -> dict[str, list[int]]:
    """{team: [QB ids by depth]} from Sleeper's live depth chart, falling back to nflverse's."""
    order: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for s in session.scalars(select(PlayerStatus).where(PlayerStatus.depth_position == "QB")):
        if s.team and s.depth_order is not None:
            order[s.team].append((s.depth_order, s.player_id))
    if not order:
        for d in session.scalars(select(DepthChartEntry).where(DepthChartEntry.position == "QB")):
            order[d.team].append((d.depth, d.player_id))
    return {team: [pid for _, pid in sorted(rows)] for team, rows in order.items()}


def backup_qb_availability(depth: Mapping[str, list[int]],
                           availability: Mapping[int, Availability]) -> dict[int, Availability]:
    """A backup QB plays only if the QBs ahead of him don't: his chance = P(every QB ahead sits).

    Example: Sam Darnold healthy as QB1 -> Drew Lock ~0, however well Lock played in Darnold's
    absence. Lamar Jackson 77% to play -> Tyler Huntley's chance is about 23%.
    """
    out: dict[int, Availability] = {}
    for team, qbs in depth.items():
        ahead_sits = 1.0
        for rank, pid in enumerate(qbs):
            own = availability.get(pid)
            own_prob = own.play_prob if own else 1.0
            if rank > 0:
                chance = ahead_sits * own_prob
                if rank == 1:
                    chance = max(chance, BACKUP_FLOOR * own_prob)   # QB2 also mops up mid-game injuries
                out[pid] = Availability(pid, round(min(own_prob, chance), 3), f"Backup QB (QB{rank + 1})",
                                        own.practice if own else None, own.injury if own else None, "depth chart")
            ahead_sits *= 1.0 - own_prob
    return out
