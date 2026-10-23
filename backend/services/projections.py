"""Per-player season data (actual + expected points) and the rest-of-season projection.

Actual points come from box-score stats, expected points from nflverse's ff_opportunity
model, snap share from nflverse snap counts. The projection is this app's own model and
is always labeled as such.
"""
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from statistics import mean

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import (
    Player, PlayerExpectedPointsWeekly, PlayerSnapCountsWeekly, PlayerStatsWeekly,
)
from services.league import POSITIONS
from services.scoring import ScoringSettings, expected_points, fantasy_points

ACTUAL_WEIGHT = 0.6           # share of actual PPG vs expected PPG in the current-season rate
PRIOR_STRENGTH_GAMES = 4      # how many games of evidence the prior is worth
MIN_PRIOR_GAMES = 4           # a prior season needs this many games to count
MIN_EXPECTED_COVERAGE = 0.75  # share of games that need expected points before averaging them
REGULAR_SEASON = "REG"
OPPORTUNITY_COLUMNS = (
    PlayerExpectedPointsWeekly.total_fantasy_points_exp,
    PlayerExpectedPointsWeekly.receptions_exp,
    PlayerExpectedPointsWeekly.pass_touchdown_exp,
    PlayerExpectedPointsWeekly.total_touchdown_exp,
)


@dataclass(frozen=True)
class GameLine:
    week: int
    season_type: str | None
    opponent: str | None
    fantasy_points: float
    expected_points: float | None
    expected_tds: float | None
    passing_yards: int
    passing_tds: int
    interceptions: int
    carries: int
    rushing_yards: int
    rushing_tds: int
    targets: int
    receptions: int
    receiving_yards: int
    receiving_tds: int
    target_share: float | None
    snap_pct: float | None    # 0-100

    @property
    def total_tds(self) -> int:
        return self.passing_tds + self.rushing_tds + self.receiving_tds


@dataclass(frozen=True)
class PlayerSeason:
    player_id: int
    name: str
    position: str | None
    team: str | None
    season: int
    games: tuple[GameLine, ...]   # regular-season games, ascending by week

    @property
    def games_played(self) -> int:
        return len(self.games)

    @property
    def total_points(self) -> float:
        return round(sum(g.fantasy_points for g in self.games), 2)

    @property
    def ppg(self) -> float:
        return round(self.total_points / self.games_played, 2) if self.games else 0.0

    @property
    def expected_ppg(self) -> float | None:
        return average_expected(self.games)

    @property
    def fpoe_per_game(self) -> float | None:
        return points_over_expected(self.games)

    def recent(self, count: int) -> tuple[GameLine, ...]:
        return self.games[-count:]


def expected_games(games: Sequence[GameLine]) -> list[GameLine]:
    """Games with an expected-points row. nflverse can publish them later than box scores."""
    return [g for g in games if g.expected_points is not None]


def has_expected_coverage(games: Sequence[GameLine]) -> bool:
    return bool(games) and len(expected_games(games)) >= MIN_EXPECTED_COVERAGE * len(games)


def average_expected(games: Sequence[GameLine]) -> float | None:
    """Mean expected points over the games that have them, if enough games do."""
    if not has_expected_coverage(games):
        return None
    return round(mean(g.expected_points for g in expected_games(games)), 2)


def points_over_expected(games: Sequence[GameLine]) -> float | None:
    """Actual minus expected points per game, compared over the same games."""
    if not has_expected_coverage(games):
        return None
    covered = expected_games(games)
    return round(mean(g.fantasy_points for g in covered) - mean(g.expected_points for g in covered), 2)


def _int(value: float | int | None) -> int:
    return int(value) if value is not None else 0


def _game_line(row, opportunity, snap_pct: float | None, scoring: ScoringSettings) -> GameLine:
    stats = row._mapping
    position = stats["position"]
    return GameLine(
        week=stats["week"], season_type=stats["season_type"], opponent=stats["opponent_team"],
        fantasy_points=fantasy_points(stats, position, scoring),
        expected_points=expected_points(opportunity._mapping, position, scoring) if opportunity else None,
        expected_tds=opportunity.total_touchdown_exp if opportunity else None,
        passing_yards=_int(stats["passing_yards"]), passing_tds=_int(stats["passing_tds"]),
        interceptions=_int(stats["passing_interceptions"]), carries=_int(stats["carries"]),
        rushing_yards=_int(stats["rushing_yards"]), rushing_tds=_int(stats["rushing_tds"]),
        targets=_int(stats["targets"]), receptions=_int(stats["receptions"]),
        receiving_yards=_int(stats["receiving_yards"]), receiving_tds=_int(stats["receiving_tds"]),
        target_share=stats["target_share"],
        snap_pct=round(snap_pct * 100, 1) if snap_pct is not None else None,
    )


def _side_tables(session: Session, season: int, player_ids: Iterable[int] | None):
    opp_query = select(PlayerExpectedPointsWeekly.player_id, PlayerExpectedPointsWeekly.week,
                       *OPPORTUNITY_COLUMNS).where(PlayerExpectedPointsWeekly.season == season)
    snap_query = select(PlayerSnapCountsWeekly.player_id, PlayerSnapCountsWeekly.week,
                        PlayerSnapCountsWeekly.offense_pct).where(PlayerSnapCountsWeekly.season == season)
    if player_ids is not None:
        ids = list(player_ids)
        opp_query = opp_query.where(PlayerExpectedPointsWeekly.player_id.in_(ids))
        snap_query = snap_query.where(PlayerSnapCountsWeekly.player_id.in_(ids))
    opportunity = {(r.player_id, r.week): r for r in session.execute(opp_query)}
    snaps = {(r.player_id, r.week): r.offense_pct for r in session.execute(snap_query)}
    return opportunity, snaps


def load_game_lines(session: Session, season: int, scoring: ScoringSettings,
                    player_ids: Iterable[int] | None = None, regular_season_only: bool = True,
                    positions: Sequence[str] = POSITIONS) -> dict[int, tuple[dict, list[GameLine]]]:
    """Return {player_id: (player_info, [GameLine...])} for one season."""
    ids = list(player_ids) if player_ids is not None else None
    query = (select(Player.id.label("pid"), Player.full_name, Player.position, Player.nfl_team,
                    *PlayerStatsWeekly.__table__.columns)
             .join(Player, Player.id == PlayerStatsWeekly.player_id)
             .where(PlayerStatsWeekly.season == season, Player.position.in_(positions))
             .order_by(PlayerStatsWeekly.week))
    if regular_season_only:
        query = query.where(PlayerStatsWeekly.season_type == REGULAR_SEASON)
    if ids is not None:
        query = query.where(PlayerStatsWeekly.player_id.in_(ids))
    opportunity, snaps = _side_tables(session, season, ids)

    players: dict[int, tuple[dict, list[GameLine]]] = {}
    for row in session.execute(query):
        key = (row.pid, row.week)
        info, lines = players.setdefault(row.pid, ({"name": row.full_name, "position": row.position,
                                                     "team": row.nfl_team}, []))
        lines.append(_game_line(row, opportunity.get(key), snaps.get(key), scoring))
    return players


def load_player_seasons(session: Session, season: int, scoring: ScoringSettings,
                        player_ids: Iterable[int] | None = None) -> list[PlayerSeason]:
    loaded = load_game_lines(session, season, scoring, player_ids)
    return [PlayerSeason(player_id=pid, name=info["name"], position=info["position"],
                         team=info["team"], season=season, games=tuple(lines))
            for pid, (info, lines) in loaded.items()]


def latest_season(session: Session) -> int | None:
    return session.scalar(select(func.max(PlayerStatsWeekly.season)))


def latest_week(session: Session, season: int) -> int | None:
    return session.scalar(select(func.max(PlayerStatsWeekly.week)).where(
        PlayerStatsWeekly.season == season, PlayerStatsWeekly.season_type == REGULAR_SEASON))


def available_seasons(session: Session) -> list[int]:
    return sorted(session.scalars(select(PlayerStatsWeekly.season).distinct()))


def current_rate(player: PlayerSeason) -> float:
    """This season's per-game rate, leaning on expected points because usage is stickier."""
    expected = player.expected_ppg
    if expected is None:
        return player.ppg
    return round(ACTUAL_WEIGHT * player.ppg + (1 - ACTUAL_WEIGHT) * expected, 2)


def project_ros_ppg(rate: float, games: int, prior_ppg: float) -> float:
    """Shrink the current rate toward a prior; more games played means less shrinkage."""
    weight = games / (games + PRIOR_STRENGTH_GAMES)
    return round(weight * rate + (1 - weight) * prior_ppg, 2)
