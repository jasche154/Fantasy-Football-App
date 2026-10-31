"""Opponent-adjusted strength of schedule, by position.

Raw "fantasy points allowed" flatters defenses that happened to face weak offenses. Here
each game's points scored by an offense at a position are compared with what that offense
normally scores, and each offense's normal output is in turn judged against the defenses
it faced. Alternating those two steps a few times (like a simple rating system) gives:

    defense factor 1.15 at WR = offenses score 15% more WR points than usual against it

Factors are shrunk toward 1.0 with PRIOR_GAMES of neutral evidence, so two weeks of data
can't produce extreme matchups. Without a loaded schedule every factor is 1.0 (neutral).
"""
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from types import MappingProxyType

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import NflGame
from services.league import POSITIONS
from services.projections import REGULAR_SEASON, PlayerSeason

ITERATIONS = 4
PRIOR_GAMES = 4
MIN_FACTOR, MAX_FACTOR = 0.7, 1.35
STALE_GAME_DAYS = 3


def _eastern_dst(local: datetime) -> bool:
    """US daylight time: 2 a.m. on the second Sunday of March to 2 a.m. on the first Sunday of November."""
    def nth_sunday(month: int, n: int) -> datetime:
        first = datetime(local.year, month, 1)
        return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1), hours=2)
    return nth_sunday(3, 2) <= local < nth_sunday(11, 1)


@dataclass(frozen=True)
class Game:
    week: int
    home_team: str
    away_team: str
    home_score: int | None
    away_score: int | None
    spread_line: float | None     # home team's expected margin
    total_line: float | None
    gameday: str | None = None    # "YYYY-MM-DD"
    gametime: str | None = None   # "13:00", US Eastern

    @property
    def kickoff(self) -> datetime | None:
        """Kickoff in UTC (nflverse lists US Eastern times)."""
        if not self.gameday or not self.gametime:
            return None
        try:
            local = datetime.strptime(f"{self.gameday} {self.gametime}", "%Y-%m-%d %H:%M")
        except ValueError:
            return None
        return (local + timedelta(hours=4 if _eastern_dst(local) else 5)).replace(tzinfo=timezone.utc)

    @property
    def is_final(self) -> bool:
        return self.home_score is not None and self.away_score is not None

    def implied_points(self, team: str) -> float | None:
        """Vegas implied team total from the spread and over/under."""
        if self.spread_line is None or self.total_line is None:
            return None
        sign = 1 if team == self.home_team else -1
        return round((self.total_line + sign * self.spread_line) / 2, 2)

    def points_scored(self, team: str) -> int | None:
        return self.home_score if team == self.home_team else self.away_score


@dataclass(frozen=True)
class Schedule:
    """Regular-season games for one season, indexed for matchup lookups."""

    games: tuple[Game, ...]
    opponents: Mapping[tuple[str, int], str] = field(default_factory=dict)   # (team, week) -> opponent

    def opponent(self, team: str | None, week: int) -> str | None:
        return self.opponents.get((team, week)) if team else None

    def game(self, team: str | None, week: int) -> Game | None:
        return next((g for g in self.games if g.week == week and team in (g.home_team, g.away_team)), None)

    def points_per_game(self, team: str | None) -> float | None:
        scored = [g.points_scored(team) for g in self.games if g.is_final and team in (g.home_team, g.away_team)]
        return round(sum(scored) / len(scored), 2) if scored else None

    def current_week(self, today: date | None = None) -> int | None:
        """The first week with a game not yet played.

        Unscored games more than STALE_GAME_DAYS in the past (postponed or cancelled) are
        ignored so they can't pin the current week.
        """
        cutoff = ((today or date.today()) - timedelta(days=STALE_GAME_DAYS)).isoformat()
        return next((g.week for g in self.games
                     if not g.is_final and (g.gameday is None or g.gameday >= cutoff)), None)

    def remaining_opponents(self, team: str | None, after_week: int, end_week: int) -> list[str]:
        return [self.opponents[(team, w)] for w in range(after_week + 1, end_week + 1)
                if (team, w) in self.opponents]


def load_schedule(session: Session, season: int) -> Schedule:
    games = tuple(Game(week=g.week, home_team=g.home_team, away_team=g.away_team,
                       home_score=g.home_score, away_score=g.away_score,
                       spread_line=g.spread_line, total_line=g.total_line, gameday=g.gameday,
                       gametime=g.gametime)
                  for g in session.scalars(select(NflGame).where(
                      NflGame.season == season, NflGame.game_type == REGULAR_SEASON).order_by(NflGame.week)))
    opponents = {}
    for g in games:
        opponents[(g.home_team, g.week)] = g.away_team
        opponents[(g.away_team, g.week)] = g.home_team
    return Schedule(games=games, opponents=MappingProxyType(opponents))


@dataclass(frozen=True)
class DefenseStrength:
    factors: Mapping[tuple[str, str], float]      # (defense, position) -> factor
    games: Mapping[tuple[str, str], int]
    allowed_per_game: Mapping[tuple[str, str], float]

    def factor(self, defense: str | None, position: str | None) -> float:
        return self.factors.get((defense, position), 1.0) if defense and position else 1.0

    def rank(self, defense: str | None, position: str | None) -> int | None:
        """1 = easiest matchup (gives up the most) at the position."""
        if (defense, position) not in self.factors:
            return None
        ordered = sorted((f for (d, p), f in self.factors.items() if p == position), reverse=True)
        return ordered.index(self.factors[(defense, position)]) + 1

    def schedule_factor(self, opponents: Sequence[str], position: str | None) -> float:
        """Average factor across a run of opponents (1.0 when there are none)."""
        if not opponents:
            return 1.0
        return round(sum(self.factor(o, position) for o in opponents) / len(opponents), 4)


NEUTRAL = DefenseStrength(factors=MappingProxyType({}), games=MappingProxyType({}),
                          allowed_per_game=MappingProxyType({}))


def team_position_points(players: Iterable[PlayerSeason], schedule: Schedule) -> dict[tuple[str, str, int, str], float]:
    """{(offense, defense, week, position): points the offense scored at that position}."""
    totals: dict[tuple[str, str, int, str], float] = defaultdict(float)
    for player in players:
        if player.position not in POSITIONS:
            continue
        for game in player.games:
            offense = schedule.opponent(game.opponent, game.week)   # the opponent's opponent
            if offense and game.opponent:
                totals[(offense, game.opponent, game.week, player.position)] += game.fantasy_points
    return totals


def _shrink(ratio: float, games: int) -> float:
    weight = games / (games + PRIOR_GAMES)
    return min(MAX_FACTOR, max(MIN_FACTOR, 1.0 + (ratio - 1.0) * weight))


def _solve(points: Mapping[tuple[str, str, int], float]) -> tuple[dict[str, float], dict[str, int]]:
    """Defense factors for one position, from {(offense, defense, week): points}."""
    defense_games: dict[str, int] = defaultdict(int)
    for _, defense, _ in points:
        defense_games[defense] += 1
    defense = {d: 1.0 for d in defense_games}
    offense: dict[str, float] = {}
    for _ in range(ITERATIONS):
        # An offense's usual output, with each game deflated by how generous that defense was.
        sums: dict[str, list[float]] = defaultdict(list)
        for (o, d, _), value in points.items():
            sums[o].append(value / defense[d])
        offense = {o: sum(v) / len(v) for o, v in sums.items()}
        # A defense's factor: points allowed relative to what those offenses usually score.
        allowed: dict[str, float] = defaultdict(float)
        expected: dict[str, float] = defaultdict(float)
        for (o, d, _), value in points.items():
            allowed[d] += value
            expected[d] += offense[o]
        defense = {d: _shrink(allowed[d] / expected[d], defense_games[d]) if expected[d] else 1.0
                   for d in defense_games}
    return defense, dict(defense_games)


def defense_strength(players: Iterable[PlayerSeason], schedule: Schedule) -> DefenseStrength:
    if not schedule.games:
        return NEUTRAL
    by_position: dict[str, dict[tuple[str, str, int], float]] = defaultdict(dict)
    for (offense, defense, week, position), value in team_position_points(players, schedule).items():
        by_position[position][(offense, defense, week)] = value
    factors, games, allowed = {}, {}, {}
    for position, points in by_position.items():
        solved, counts = _solve(points)
        totals: dict[str, float] = defaultdict(float)
        for (_, d, _), value in points.items():
            totals[d] += value
        for d, value in solved.items():
            factors[(d, position)] = round(value, 4)
            games[(d, position)] = counts[d]
            allowed[(d, position)] = round(totals[d] / counts[d], 2)
    return DefenseStrength(factors=MappingProxyType(factors), games=MappingProxyType(games),
                           allowed_per_game=MappingProxyType(allowed))
