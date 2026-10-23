"""Single-week building blocks: scoring projected stat lines, matchups, and this app's model.

Projected stat lines use these keys (all per game):
    pass_yds, pass_tds, ints, rush_yds, rush_tds, rec, rec_yds, rec_tds,
    scrimmage_tds (rush + receiving TDs, from anytime-TD props), fumbles_lost, two_pt

The in-house model starts from the player's rest-of-season rate (before schedule
adjustment), keeps the shape of their season stat line, then applies this week's matchup:

    model = base_ros_ppg * sos_factor ** SOS_WEIGHT * implied_factor ** IMPLIED_TOTAL_WEIGHT

sos_factor is the opponent-adjusted defense factor at the player's position; implied_factor
is the Vegas implied team total divided by the team's scoring average.
"""
import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from services.projections import ACTUAL_WEIGHT, PRIOR_STRENGTH_GAMES, PlayerSeason
from services.schedule_strength import DefenseStrength, Schedule
from services.scoring import ScoringSettings, reception_points

# Tuned with `python -m services.backtest --season 2025 --ablation` (3,641 player-weeks, weeks 4-18),
# then checked on 2024 without re-tuning:
#   season-long rate only             2025 MAE 6.81, rank r 0.359 | 2024 MAE 6.69, rank r 0.394
#   half-life 4 + injuries + implied  2025 MAE 5.99, rank r 0.504 | 2024 MAE 6.03, rank r 0.528
#   same with SOS^0.5 (chosen)        2025 MAE 6.01, rank r 0.502 | 2024 MAE 6.05, rank r 0.526
# Vegas implied totals already price the opposing defense, so schedule strength adds little on
# top; it is kept at 0.5 because the user asked for schedule to weigh in, at a ~0.3% MAE cost.
SOS_WEIGHT = 0.5
IMPLIED_TOTAL_WEIGHT = 0.5
RECENCY_HALF_LIFE: float | None = 4.0     # games; None = every game this season counts equally
RANGES_PATH = Path(__file__).with_name("projection_ranges.json")


@dataclass(frozen=True)
class WeeklyModelSettings:
    """Tunable model knobs (see services/backtest.py for how they were chosen)."""

    half_life: float | None = RECENCY_HALF_LIFE
    sos_weight: float = SOS_WEIGHT
    implied_weight: float = IMPLIED_TOTAL_WEIGHT
    use_injuries: bool = True

    def describe(self) -> str:
        recency = "season" if self.half_life is None else f"half-life {self.half_life:g}"
        return (f"{recency}, sos^{self.sos_weight:g}, implied^{self.implied_weight:g}, "
                f"injuries {'on' if self.use_injuries else 'off'}")


def weekly_rate(player: PlayerSeason, prior_ppg: float | None, half_life: float | None = RECENCY_HALF_LIFE) -> float:
    """Recency-weighted points per game, shrunk toward last season.

    Each game counts 0.6 x actual + 0.4 x expected points (usage is stickier than results);
    a game half_life games ago counts half as much as the latest. With no prior, no shrinkage.
    """
    games = player.games
    if not games:
        return prior_ppg or 0.0
    values = [ACTUAL_WEIGHT * g.fantasy_points + (1 - ACTUAL_WEIGHT) * g.expected_points
              if g.expected_points is not None else g.fantasy_points for g in games]
    weights = [1.0 if half_life is None else 0.5 ** ((len(games) - 1 - i) / half_life) for i in range(len(games))]
    rate = sum(w * v for w, v in zip(weights, values)) / sum(weights)
    if prior_ppg is None:
        return round(rate, 2)
    shrink = len(games) / (len(games) + PRIOR_STRENGTH_GAMES)
    return round(shrink * rate + (1 - shrink) * prior_ppg, 2)


@lru_cache(maxsize=1)
def projection_ranges() -> dict[str, dict[str, float]]:
    """Floor/ceiling multipliers by position, measured by the backtest."""
    try:
        return json.loads(RANGES_PATH.read_text(encoding="utf-8"))["positions"]
    except (OSError, ValueError, KeyError):
        return {}
IMPLIED_FACTOR_LIMITS = (0.75, 1.25)
# A props line only counts when the position's main yardage market is posted.
PRIMARY_STAT = {"QB": "pass_yds", "RB": "rush_yds", "WR": "rec_yds", "TE": "rec_yds"}
TD_KEYS = ("rush_tds", "rec_tds")


def points_from_stats(stats: Mapping[str, float], position: str | None, scoring: ScoringSettings) -> float:
    get = lambda key: float(stats.get(key) or 0.0)  # noqa: E731
    points = (get("pass_yds") * scoring.pass_yd + get("pass_tds") * scoring.pass_td
              + get("ints") * scoring.interception + get("rush_yds") * scoring.rush_yd
              + get("rush_tds") * scoring.rush_td + get("rec") * reception_points(position, scoring)
              + get("rec_yds") * scoring.rec_yd + get("rec_tds") * scoring.rec_td
              + get("scrimmage_tds") * scoring.rush_td + get("fumbles_lost") * scoring.fumble_lost
              + get("two_pt") * scoring.two_point)
    return round(points, 2)


@dataclass(frozen=True)
class Matchup:
    opponent: str
    home: bool
    sos_factor: float
    sos_rank: int | None          # 1 = easiest defense at the position
    implied_total: float | None
    team_ppg: float | None
    game_final: bool

    @property
    def implied_factor(self) -> float:
        if not self.implied_total or not self.team_ppg:
            return 1.0
        low, high = IMPLIED_FACTOR_LIMITS
        return min(high, max(low, self.implied_total / self.team_ppg))

    @property
    def multiplier(self) -> float:
        return self.sos_factor ** SOS_WEIGHT * self.implied_factor ** IMPLIED_TOTAL_WEIGHT


def matchup_for(team: str | None, position: str | None, week: int, schedule: Schedule,
                defense: DefenseStrength) -> Matchup | None:
    """None when the team has no game that week (bye) or isn't in the schedule."""
    game = schedule.game(team, week)
    if game is None or team is None:
        return None
    opponent = game.away_team if game.home_team == team else game.home_team
    return Matchup(opponent=opponent, home=game.home_team == team,
                   sos_factor=defense.factor(opponent, position), sos_rank=defense.rank(opponent, position),
                   implied_total=game.implied_points(team), team_ppg=schedule.points_per_game(team),
                   game_final=game.is_final)


def season_stat_line(player: PlayerSeason) -> dict[str, float]:
    """Per-game averages of the player's regular-season box scores."""
    games = player.games
    if not games:
        return {}
    totals = {
        "pass_yds": sum(g.passing_yards for g in games), "pass_tds": sum(g.passing_tds for g in games),
        "ints": sum(g.interceptions for g in games), "rush_yds": sum(g.rushing_yards for g in games),
        "rush_tds": sum(g.rushing_tds for g in games), "rec": sum(g.receptions for g in games),
        "rec_yds": sum(g.receiving_yards for g in games), "rec_tds": sum(g.receiving_tds for g in games),
    }
    return {key: value / len(games) for key, value in totals.items()}


def model_stats(player: PlayerSeason, base_ppg: float, matchup: Matchup,
                scoring: ScoringSettings) -> dict[str, float]:
    """The season stat line rescaled to the projected rate, then adjusted for the matchup."""
    line = season_stat_line(player)
    line_points = points_from_stats(line, player.position, scoring)
    if line_points <= 0:
        return {}
    scale = base_ppg / line_points * matchup.multiplier
    return {key: value * scale for key, value in line.items()}


def props_line(props: Mapping[str, float], position: str | None,
               fallback: Mapping[str, float]) -> dict[str, float] | None:
    """Props stats with gaps (e.g. an RB's receiving line) filled from the model.

    None when the position's primary market isn't posted, since a lone anytime-TD price
    says too little about a player's week to stand in for a projection.
    """
    if not position or PRIMARY_STAT[position] not in props:
        return None
    filled = dict(fallback)
    if "scrimmage_tds" in props:
        for key in TD_KEYS:
            filled.pop(key, None)
    filled.update(props)
    return filled
