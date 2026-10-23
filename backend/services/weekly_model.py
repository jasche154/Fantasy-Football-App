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
from collections.abc import Mapping
from dataclasses import dataclass

from services.projections import PlayerSeason
from services.schedule_strength import DefenseStrength, Schedule
from services.scoring import ScoringSettings, reception_points

SOS_WEIGHT = 1.0
IMPLIED_TOTAL_WEIGHT = 0.5
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
