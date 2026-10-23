"""Whitelisted, volume-weighted advanced-stat leaderboards.

nflverse Next Gen Stats publish weekly rows plus a week-0 row with the official season
totals. Weekly rows only appear for players who meet a weekly volume minimum, so the
week-0 row is more complete (Derrick Henry 2025: 307 attempts vs. 299 in weekly rows).
Each player's season value comes from their week-0 row when one exists, and otherwise
from a volume-weighted aggregate of their weekly rows (sum of RYOE / sum of attempts,
not the mean of weekly rates). Weekly and week-0 rows are never mixed.

Exception: target share, air-yards share and WOPR are per-game averages (labeled as such),
because nflverse publishes them per game and the stats table has no team totals to weight by.
"""
from dataclasses import dataclass

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from database.models import (
    Player, PlayerPassingAdvancedWeekly as Passing, PlayerReceivingAdvancedWeekly as Receiving,
    PlayerRushingAdvancedWeekly as Rushing, PlayerStatsWeekly as Stats,
)
from services.league import POSITIONS

REGULAR_SEASON = "REG"


def ratio(numerator, denominator) -> ColumnElement:
    return func.sum(numerator) * 1.0 / func.nullif(func.sum(denominator), 0)


def weighted(column, weight) -> ColumnElement:
    """Volume-weighted mean that ignores weeks where the metric is missing."""
    return (func.sum(column * weight) * 1.0
            / func.nullif(func.sum(case((column.is_not(None), weight), else_=0)), 0))


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    description: str
    value: ColumnElement
    volume: ColumnElement
    volume_label: str
    default_min_volume: int
    format: str = "decimal"
    higher_is_better: bool = True
    # Expression for an NGS week-0 season-total row, when it differs from `value`.
    season_value: ColumnElement | None = None


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    model: type
    metrics: tuple[Metric, ...]


RUSH_ATT, TARGETS, PASS_ATT = func.sum(Rushing.rush_attempts), func.sum(Receiving.targets), func.sum(Passing.attempts)

CATEGORIES: tuple[Category, ...] = (
    Category("rushing", "Rushing (Next Gen Stats)", Rushing, (
        # NGS's published per-attempt figure leaves out untracked carries, so prefer it for season rows.
        Metric("ryoe_per_att", "Rush yards over expected / att",
               "Yards gained per carry beyond what NGS expected given blockers and defenders.",
               ratio(Rushing.rush_yards_over_expected, Rushing.rush_attempts), RUSH_ATT, "Attempts", 50,
               season_value=func.max(Rushing.rush_yards_over_expected_per_att)),
        Metric("rush_yards_over_expected", "Rush yards over expected (total)",
               "Season total of yards beyond the NGS expectation.",
               func.sum(Rushing.rush_yards_over_expected), RUSH_ATT, "Attempts", 50),
        Metric("rush_pct_over_expected", "Carries beating expectation",
               "Share of carries that gained more than expected.",
               100 * weighted(Rushing.rush_pct_over_expected, Rushing.rush_attempts), RUSH_ATT,
               "Attempts", 50, "percent"),
        Metric("yards_per_carry", "Yards per carry", "Rushing yards divided by attempts.",
               ratio(Rushing.rush_yards, Rushing.rush_attempts), RUSH_ATT, "Attempts", 50),
        Metric("efficiency", "Rushing efficiency",
               "Distance traveled per rushing yard gained. Lower means more north-south running.",
               weighted(Rushing.efficiency, Rushing.rush_attempts), RUSH_ATT, "Attempts", 50,
               higher_is_better=False),
        Metric("avg_time_to_los", "Time to line of scrimmage (s)",
               "Average seconds from handoff to crossing the line of scrimmage.",
               weighted(Rushing.avg_time_to_los, Rushing.rush_attempts), RUSH_ATT, "Attempts", 50,
               higher_is_better=False),
        Metric("stacked_box_rate", "Carries vs 8+ defenders",
               "Share of carries into a stacked box (8 or more defenders), a measure of difficulty.",
               weighted(Rushing.percent_attempts_gte_eight_defenders, Rushing.rush_attempts), RUSH_ATT,
               "Attempts", 50, "percent"),
    )),
    Category("receiving", "Receiving (Next Gen Stats)", Receiving, (
        Metric("avg_separation", "Separation at catch (yd)",
               "Average distance to the nearest defender when the ball arrives.",
               weighted(Receiving.avg_separation, Receiving.targets), TARGETS, "Targets", 25),
        Metric("avg_yac_above_expectation", "YAC over expected",
               "Yards after catch per reception beyond the NGS expectation.",
               weighted(Receiving.avg_yac_above_expectation, Receiving.receptions), TARGETS, "Targets", 25),
        Metric("catch_percentage", "Catch rate", "Receptions divided by targets.",
               100 * ratio(Receiving.receptions, Receiving.targets), TARGETS, "Targets", 25, "percent"),
        Metric("avg_intended_air_yards", "Intended air yards / target (aDOT)",
               "Average depth of target.", weighted(Receiving.avg_intended_air_yards, Receiving.targets),
               TARGETS, "Targets", 25),
        Metric("air_yards_share", "Share of team intended air yards",
               "Share of the team's intended air yards directed at this player.",
               weighted(Receiving.percent_share_of_intended_air_yards, Receiving.targets), TARGETS,
               "Targets", 25, "percent"),
        Metric("avg_cushion", "Cushion at snap (yd)", "Average distance from the defender at the snap.",
               weighted(Receiving.avg_cushion, Receiving.targets), TARGETS, "Targets", 25),
    )),
    Category("passing", "Passing (Next Gen Stats)", Passing, (
        Metric("cpoe", "Completion % over expected",
               "Completion rate above what NGS expected from throw difficulty (percentage points).",
               weighted(Passing.completion_percentage_above_expectation, Passing.attempts), PASS_ATT,
               "Attempts", 75),
        Metric("avg_time_to_throw", "Time to throw (s)", "Average seconds from snap to release.",
               weighted(Passing.avg_time_to_throw, Passing.attempts), PASS_ATT, "Attempts", 75,
               higher_is_better=False),
        Metric("aggressiveness", "Aggressiveness",
               "Share of throws into tight windows (a defender within 1 yard).",
               weighted(Passing.aggressiveness, Passing.attempts), PASS_ATT, "Attempts", 75, "percent"),
        Metric("avg_intended_air_yards", "Intended air yards / att", "Average depth of throw.",
               weighted(Passing.avg_intended_air_yards, Passing.attempts), PASS_ATT, "Attempts", 75),
        Metric("avg_air_yards_to_sticks", "Air yards to the sticks",
               "Average throw depth relative to the first-down marker.",
               weighted(Passing.avg_air_yards_to_sticks, Passing.attempts), PASS_ATT, "Attempts", 75),
        Metric("passer_rating", "Passer rating (attempt-weighted)",
               "Weekly passer ratings averaged by attempts (an approximation of the season rating).",
               weighted(Passing.passer_rating, Passing.attempts), PASS_ATT, "Attempts", 75),
    )),
    Category("usage", "Usage & efficiency", Stats, (
        Metric("target_share", "Target share (per-game avg)", "Average of each game's share of team targets.",
               100 * func.avg(Stats.target_share), func.sum(Stats.targets), "Targets", 20, "percent"),
        Metric("air_yards_share", "Air yards share (per-game avg)", "Average of each game's share of team air yards.",
               100 * func.avg(Stats.air_yards_share), func.sum(Stats.targets), "Targets", 20, "percent"),
        Metric("wopr", "WOPR (per-game avg)", "Weighted opportunity rating, 1.5 × target share + 0.7 × air yards share, averaged per game.",
               func.avg(Stats.wopr), func.sum(Stats.targets), "Targets", 20),
        Metric("receiving_epa_per_target", "Receiving EPA / target",
               "Expected points added on targets.", ratio(Stats.receiving_epa, Stats.targets),
               func.sum(Stats.targets), "Targets", 30),
        Metric("rushing_epa_per_carry", "Rushing EPA / carry", "Expected points added per carry.",
               ratio(Stats.rushing_epa, Stats.carries), func.sum(Stats.carries), "Carries", 50),
        Metric("passing_epa_per_play", "Passing EPA / dropback",
               "Expected points added per pass attempt or sack.",
               ratio(Stats.passing_epa, Stats.attempts + Stats.sacks_suffered),
               func.sum(Stats.attempts + Stats.sacks_suffered), "Dropbacks", 100),
        Metric("ppr_points_per_game", "PPR points / game", "Actual PPR fantasy points per game played.",
               func.sum(Stats.fantasy_points_ppr) * 1.0 / func.count(Stats.id), func.count(Stats.id),
               "Games", 3),
    )),
)
CATEGORY_BY_KEY = {c.key: c for c in CATEGORIES}


@dataclass(frozen=True)
class LeaderboardRow:
    rank: int
    player_id: int
    name: str
    position: str | None
    team: str | None
    value: float
    volume: float


def get_metric(category_key: str, metric_key: str) -> tuple[Category, Metric]:
    category = CATEGORY_BY_KEY.get(category_key)
    if category is None:
        raise ValueError(f"Unknown category '{category_key}'. Valid: {sorted(CATEGORY_BY_KEY)}")
    for metric in category.metrics:
        if metric.key == metric_key:
            return category, metric
    raise ValueError(f"Unknown metric '{metric_key}' for {category_key}. "
                     f"Valid: {[m.key for m in category.metrics]}")


def _player_values(session: Session, category: Category, value: ColumnElement, volume: ColumnElement,
                   season: int, season_totals: bool, position: str | None) -> dict[int, tuple]:
    """{player_id: row with value and volume} from either weekly rows or NGS week-0 total rows."""
    model = category.model
    filters = [model.season == season, model.season_type == REGULAR_SEASON]
    if model is not Stats:
        filters.append(model.week == 0 if season_totals else model.week > 0)
    query = (select(Player.id, Player.full_name, Player.position, Player.nfl_team,
                    value.label("value"), volume.label("volume"))
             .join(Player, Player.id == model.player_id).where(*filters).group_by(Player.id))
    if position:
        query = query.where(Player.position == position)
    return {r.id: r for r in session.execute(query)}


def leaderboard(session: Session, category_key: str, metric_key: str, season: int,
                min_volume: int | None = None, limit: int | None = 25, ascending: bool = False,
                position: str | None = None) -> list[LeaderboardRow]:
    category, metric = get_metric(category_key, metric_key)
    if position is not None and position not in POSITIONS:
        raise ValueError(f"Unknown position '{position}'. Valid: {list(POSITIONS)}")
    threshold = metric.default_min_volume if min_volume is None else min_volume

    rows = {}
    if category.model is not Stats:
        season_value = metric.value if metric.season_value is None else metric.season_value
        rows = _player_values(session, category, season_value, metric.volume, season, True, position)
    weekly = _player_values(session, category, metric.value, metric.volume, season, False, position)
    merged = [*rows.values(), *(r for pid, r in weekly.items() if pid not in rows)]

    qualified = [r for r in merged if r.value is not None and (r.volume or 0) >= threshold]
    qualified.sort(key=lambda r: r.full_name)
    qualified.sort(key=lambda r: r.value, reverse=not ascending)
    if limit is not None:
        qualified = qualified[:limit]
    return [LeaderboardRow(rank=i, player_id=r.id, name=r.full_name, position=r.position,
                           team=r.nfl_team, value=round(float(r.value), 3), volume=float(r.volume or 0))
            for i, r in enumerate(qualified, start=1)]


def catalog() -> list[dict]:
    return [{"key": c.key, "label": c.label, "metrics": [
        {"key": m.key, "label": m.label, "description": m.description, "volume_label": m.volume_label,
         "default_min_volume": m.default_min_volume, "format": m.format,
         "higher_is_better": m.higher_is_better} for m in c.metrics]} for c in CATEGORIES]
