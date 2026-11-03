"""Per-game usage rows for one position-season, and "what changed" over a player's last few games.

Rows come straight from nflverse box scores (player_stats_weekly, regular season only) plus
snap counts. Shares (target, air-yard, snap) are 0-1 fractions in the database and are
reported here as percentages.
"""
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import mean

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import Player, PlayerSnapCountsWeekly, PlayerStatsWeekly
from services.projections import REGULAR_SEASON
from services.scoring import ScoringSettings, fantasy_points

RECENT_GAMES = 3                 # "last 3 games"
MIN_BASELINE_GAMES = 2           # games before the recent window needed for a comparison
# A change at least this big is flagged as a big move (percentage points for shares, per game otherwise).
SNAP_SHARE_FLAG = 10.0
TARGET_SHARE_FLAG = 5.0
AIR_YARDS_SHARE_FLAG = 8.0
CARRIES_FLAG = 4.0
TARGETS_FLAG = 2.0
PASS_ATTEMPTS_FLAG = 5.0
STAT_COLUMNS = (
    "attempts", "completions", "passing_yards", "passing_tds", "passing_interceptions", "passing_air_yards",
    "sacks_suffered", "sack_fumbles_lost", "carries", "rushing_yards", "rushing_tds", "rushing_fumbles_lost",
    "targets", "receptions", "receiving_yards", "receiving_tds", "receiving_air_yards",
    "receiving_yards_after_catch", "receiving_fumbles_lost",
)
SCORING_ONLY_COLUMNS = ("passing_2pt_conversions", "rushing_2pt_conversions", "receiving_2pt_conversions",
                        "special_teams_tds")


@dataclass(frozen=True)
class UsageGame:
    week: int
    stats: dict[str, float]          # STAT_COLUMNS, missing values as 0
    fantasy_points: float
    target_share: float | None       # percent
    air_yards_share: float | None    # percent
    snap_share: float | None         # percent


def _pct(value: float | None) -> float | None:
    return round(value * 100, 1) if value is not None else None


def load_usage(session: Session, season: int, position: str,
               scoring: ScoringSettings) -> dict[int, list[UsageGame]]:
    """{player_id: [UsageGame...] ascending by week} for every player at one position."""
    stat_rows = session.execute(
        select(PlayerStatsWeekly).join(Player, Player.id == PlayerStatsWeekly.player_id)
        .where(PlayerStatsWeekly.season == season, PlayerStatsWeekly.season_type == REGULAR_SEASON,
               Player.position == position)
        .order_by(PlayerStatsWeekly.week)).scalars()
    snaps = {(r.player_id, r.week): r.offense_pct for r in session.execute(
        select(PlayerSnapCountsWeekly.player_id, PlayerSnapCountsWeekly.week, PlayerSnapCountsWeekly.offense_pct)
        .join(Player, Player.id == PlayerSnapCountsWeekly.player_id)
        .where(PlayerSnapCountsWeekly.season == season, Player.position == position))}
    players: dict[int, list[UsageGame]] = {}
    for row in stat_rows:
        raw = {c: getattr(row, c) for c in STAT_COLUMNS + SCORING_ONLY_COLUMNS}
        players.setdefault(row.player_id, []).append(UsageGame(
            week=row.week, stats={c: float(raw[c] or 0) for c in STAT_COLUMNS},
            fantasy_points=fantasy_points(raw, position, scoring),
            target_share=_pct(row.target_share), air_yards_share=_pct(row.air_yards_share),
            snap_share=_pct(snaps.get((row.player_id, row.week)))))
    return players


@dataclass(frozen=True)
class ChangeMetric:
    key: str
    label: str
    unit: str          # "%" for shares (delta in percentage points), "/g" for per-game counts
    flag_at: float
    positions: tuple[str, ...]

    def value(self, games: Sequence[UsageGame]) -> float | None:
        if self.unit == "%":
            values = [getattr(g, self.key) for g in games if getattr(g, self.key) is not None]
            return round(mean(values), 1) if values else None
        return round(mean(g.stats[self.key] for g in games), 1) if games else None


ALL = ("QB", "RB", "WR", "TE")
CHANGE_METRICS = (
    ChangeMetric("snap_share", "Snap share", "%", SNAP_SHARE_FLAG, ALL),
    ChangeMetric("target_share", "Target share", "%", TARGET_SHARE_FLAG, ("RB", "WR", "TE")),
    ChangeMetric("air_yards_share", "Air-yard share", "%", AIR_YARDS_SHARE_FLAG, ("WR", "TE")),
    ChangeMetric("targets", "Targets / game", "/g", TARGETS_FLAG, ("RB", "WR", "TE")),
    ChangeMetric("carries", "Carries / game", "/g", CARRIES_FLAG, ("QB", "RB")),
    ChangeMetric("attempts", "Pass attempts / game", "/g", PASS_ATTEMPTS_FLAG, ("QB",)),
)


def what_changed(games: Sequence[UsageGame], position: str | None) -> dict:
    """The last RECENT_GAMES games against every earlier game this season, with big moves flagged."""
    recent, before = list(games[-RECENT_GAMES:]), list(games[:-RECENT_GAMES])
    base = {"recent_games": RECENT_GAMES, "recent_weeks": [g.week for g in recent], "baseline_games": len(before)}
    if len(recent) < RECENT_GAMES or len(before) < MIN_BASELINE_GAMES:
        return {**base, "metrics": [],
                "note": f"Needs {RECENT_GAMES + MIN_BASELINE_GAMES}+ games this season to compare "
                        f"the last {RECENT_GAMES} with the games before them."}
    metrics = []
    for metric in CHANGE_METRICS:
        if position not in metric.positions:
            continue
        now, then = metric.value(recent), metric.value(before)
        if now is None or then is None or (now == 0 and then == 0):
            continue
        delta = round(now - then, 1)
        flag = ("up" if delta > 0 else "down") if abs(delta) >= metric.flag_at else None
        metrics.append({"key": metric.key, "label": metric.label, "unit": metric.unit, "recent": now,
                        "before": then, "delta": delta, "flag": flag, "flag_at": metric.flag_at})
    return {**base, "metrics": metrics, "note": None}
