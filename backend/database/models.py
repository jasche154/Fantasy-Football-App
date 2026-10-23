from sqlalchemy import ForeignKey, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(unique=True)
    hashed_password: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
class PlatformConnection(Base):
    __tablename__ = "platform_connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    platform: Mapped[str]
    access_token: Mapped[str | None]
    refresh_token: Mapped[str | None]
    token_expires_at: Mapped[datetime | None]
    
class League(Base):
    __tablename__ = "leagues"

    id: Mapped[int] = mapped_column(primary_key=True)
    platform_connection_id: Mapped[int] = mapped_column(ForeignKey("platform_connections.id"))
    platform_league_id: Mapped[str]
    name: Mapped[str]
    season: Mapped[int]
    scoring_settings: Mapped[str | None]
    
class Team(Base):
    __tablename__ = "teams"
    id: Mapped[int] = mapped_column(primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id"))
    platform_team_id: Mapped[str]
    name: Mapped[str | None]
    owner_display_name: Mapped[str | None]
    is_users_team: Mapped[bool] = mapped_column(default=False)
    
class Player(Base):
    __tablename__ = "players"
    __table_args__ = (Index("ix_players_gsis_id", "gsis_id", unique=True),)
    id: Mapped[int] = mapped_column(primary_key=True)
    gsis_id: Mapped[str | None]
    yahoo_player_id: Mapped[str | None]
    sleeper_player_id: Mapped[str | None]
    pfr_player_id: Mapped[str | None]
    fantasypros_id: Mapped[str | None]
    espn_player_id: Mapped[str | None]
    full_name: Mapped[str]
    position: Mapped[str | None]
    nfl_team: Mapped[str | None]


class Roster(Base):
    __tablename__ = "rosters"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    week: Mapped[int]
    roster_slot: Mapped[str]


class PlayerStatsWeekly(Base):
    __tablename__ = "player_stats_weekly"
    __table_args__ = (Index("ix_player_stats_weekly_player_season_week", "player_id", "season", "week"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    season: Mapped[int]
    week: Mapped[int]
    season_type: Mapped[str | None]
    game_id: Mapped[str | None]
    opponent_team: Mapped[str | None]

    completions: Mapped[int | None]
    attempts: Mapped[int | None]
    passing_yards: Mapped[int | None]
    passing_tds: Mapped[int | None]
    passing_interceptions: Mapped[int | None]
    sacks_suffered: Mapped[int | None]
    sack_yards_lost: Mapped[int | None]
    sack_fumbles: Mapped[int | None]
    sack_fumbles_lost: Mapped[int | None]
    passing_air_yards: Mapped[int | None]
    passing_yards_after_catch: Mapped[int | None]
    passing_first_downs: Mapped[int | None]
    passing_epa: Mapped[float | None]
    passing_cpoe: Mapped[float | None]
    passing_2pt_conversions: Mapped[int | None]
    pacr: Mapped[float | None]
    passing_10: Mapped[int | None]
    passing_16: Mapped[int | None]
    passing_20: Mapped[int | None]
    passing_40: Mapped[int | None]

    carries: Mapped[int | None]
    rushing_yards: Mapped[int | None]
    rushing_tds: Mapped[int | None]
    rushing_fumbles: Mapped[int | None]
    rushing_fumbles_lost: Mapped[int | None]
    rushing_first_downs: Mapped[int | None]
    rushing_epa: Mapped[float | None]
    rushing_2pt_conversions: Mapped[int | None]
    rushing_10: Mapped[int | None]
    rushing_12: Mapped[int | None]
    rushing_20: Mapped[int | None]
    rushing_40: Mapped[int | None]

    receptions: Mapped[int | None]
    targets: Mapped[int | None]
    receiving_yards: Mapped[int | None]
    receiving_tds: Mapped[int | None]
    receiving_fumbles: Mapped[int | None]
    receiving_fumbles_lost: Mapped[int | None]
    receiving_air_yards: Mapped[int | None]
    receiving_yards_after_catch: Mapped[int | None]
    receiving_first_downs: Mapped[int | None]
    receiving_epa: Mapped[float | None]
    receiving_2pt_conversions: Mapped[int | None]
    receiving_10: Mapped[int | None]
    receiving_16: Mapped[int | None]
    receiving_20: Mapped[int | None]
    receiving_40: Mapped[int | None]
    racr: Mapped[float | None]
    target_share: Mapped[float | None]
    air_yards_share: Mapped[float | None]
    wopr: Mapped[float | None]

    special_teams_tds: Mapped[int | None]
    fantasy_points: Mapped[float | None]
    fantasy_points_ppr: Mapped[float | None]


class PlayerPassingAdvancedWeekly(Base):
    __tablename__ = "player_passing_advanced_weekly"
    __table_args__ = (Index("ix_player_passing_advanced_weekly_player_season_week", "player_id", "season", "week"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    season: Mapped[int]
    week: Mapped[int]
    season_type: Mapped[str | None]

    avg_time_to_throw: Mapped[float | None]
    avg_completed_air_yards: Mapped[float | None]
    avg_intended_air_yards: Mapped[float | None]
    avg_air_yards_differential: Mapped[float | None]
    aggressiveness: Mapped[float | None]
    max_completed_air_distance: Mapped[float | None]
    avg_air_yards_to_sticks: Mapped[float | None]
    attempts: Mapped[int | None]
    pass_yards: Mapped[int | None]
    pass_touchdowns: Mapped[int | None]
    interceptions: Mapped[int | None]
    passer_rating: Mapped[float | None]
    completions: Mapped[int | None]
    completion_percentage: Mapped[float | None]
    expected_completion_percentage: Mapped[float | None]
    completion_percentage_above_expectation: Mapped[float | None]
    avg_air_distance: Mapped[float | None]
    max_air_distance: Mapped[float | None]


class PlayerReceivingAdvancedWeekly(Base):
    __tablename__ = "player_receiving_advanced_weekly"
    __table_args__ = (Index("ix_player_receiving_advanced_weekly_player_season_week", "player_id", "season", "week"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    season: Mapped[int]
    week: Mapped[int]
    season_type: Mapped[str | None]

    avg_cushion: Mapped[float | None]
    avg_separation: Mapped[float | None]
    avg_intended_air_yards: Mapped[float | None]
    percent_share_of_intended_air_yards: Mapped[float | None]
    receptions: Mapped[int | None]
    targets: Mapped[int | None]
    catch_percentage: Mapped[float | None]
    yards: Mapped[int | None]
    rec_touchdowns: Mapped[int | None]
    avg_yac: Mapped[float | None]
    avg_expected_yac: Mapped[float | None]
    avg_yac_above_expectation: Mapped[float | None]


class PlayerRushingAdvancedWeekly(Base):
    __tablename__ = "player_rushing_advanced_weekly"
    __table_args__ = (Index("ix_player_rushing_advanced_weekly_player_season_week", "player_id", "season", "week"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    season: Mapped[int]
    week: Mapped[int]
    season_type: Mapped[str | None]

    efficiency: Mapped[float | None]
    percent_attempts_gte_eight_defenders: Mapped[float | None]
    avg_time_to_los: Mapped[float | None]
    rush_attempts: Mapped[int | None]
    rush_yards: Mapped[int | None]
    avg_rush_yards: Mapped[float | None]
    rush_touchdowns: Mapped[int | None]
    expected_rush_yards: Mapped[float | None]
    rush_yards_over_expected: Mapped[float | None]
    rush_yards_over_expected_per_att: Mapped[float | None]
    rush_pct_over_expected: Mapped[float | None]


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id"))
    week: Mapped[int]
    recommendation_type: Mapped[str]          # renamed from `type`, per earlier note
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    explanation_text: Mapped[str | None]
    supporting_data: Mapped[str | None]        # JSON stored as text, same as before
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class PlayerExpectedPointsWeekly(Base):
    __tablename__ = "player_expected_points_weekly"
    __table_args__ = (Index("ix_player_expected_points_weekly_player_season_week", "player_id", "season", "week"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    season: Mapped[int]
    week: Mapped[int]
    game_id: Mapped[str | None]
    posteam: Mapped[str | None]

    # expected
    pass_completions_exp: Mapped[float | None]
    receptions_exp: Mapped[float | None]
    pass_yards_gained_exp: Mapped[float | None]
    rec_yards_gained_exp: Mapped[float | None]
    rush_yards_gained_exp: Mapped[float | None]
    pass_touchdown_exp: Mapped[float | None]
    rec_touchdown_exp: Mapped[float | None]
    rush_touchdown_exp: Mapped[float | None]
    pass_first_down_exp: Mapped[float | None]
    rec_first_down_exp: Mapped[float | None]
    rush_first_down_exp: Mapped[float | None]
    pass_interception_exp: Mapped[float | None]
    rec_interception_exp: Mapped[float | None]
    pass_fantasy_points_exp: Mapped[float | None]
    rec_fantasy_points_exp: Mapped[float | None]
    rush_fantasy_points_exp: Mapped[float | None]
    total_yards_gained_exp: Mapped[float | None]
    total_touchdown_exp: Mapped[float | None]
    total_first_down_exp: Mapped[float | None]
    total_fantasy_points_exp: Mapped[float | None]

    # diff (actual minus expected, precomputed by the source)
    pass_completions_diff: Mapped[float | None]
    receptions_diff: Mapped[float | None]
    pass_yards_gained_diff: Mapped[float | None]
    rec_yards_gained_diff: Mapped[float | None]
    rush_yards_gained_diff: Mapped[float | None]
    pass_touchdown_diff: Mapped[float | None]
    rec_touchdown_diff: Mapped[float | None]
    rush_touchdown_diff: Mapped[float | None]
    pass_first_down_diff: Mapped[float | None]
    rec_first_down_diff: Mapped[float | None]
    rush_first_down_diff: Mapped[float | None]
    pass_interception_diff: Mapped[float | None]
    rec_interception_diff: Mapped[float | None]
    pass_fantasy_points_diff: Mapped[float | None]
    rec_fantasy_points_diff: Mapped[float | None]
    rush_fantasy_points_diff: Mapped[float | None]
    total_yards_gained_diff: Mapped[float | None]
    total_touchdown_diff: Mapped[float | None]
    total_first_down_diff: Mapped[float | None]
    total_fantasy_points_diff: Mapped[float | None]


class PlayerSnapCountsWeekly(Base):
    __tablename__ = "player_snap_counts_weekly"
    __table_args__ = (Index("ix_player_snap_counts_weekly_player_season_week", "player_id", "season", "week"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    season: Mapped[int]
    week: Mapped[int]
    game_type: Mapped[str | None]
    team: Mapped[str | None]
    opponent: Mapped[str | None]
    offense_snaps: Mapped[int | None]
    offense_pct: Mapped[float | None]          # 0-1 fraction, as published by nflverse


class ExpertRanking(Base):
    """A snapshot of FantasyPros expert consensus rankings (ECR)."""

    __tablename__ = "expert_rankings"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    fantasypros_id: Mapped[str | None]
    player_name: Mapped[str]
    position: Mapped[str | None]
    team: Mapped[str | None]
    source: Mapped[str]                        # "nflverse_ffrankings" or "fantasypros_api"
    ranking_type: Mapped[str]                  # e.g. "weekly", "ros"
    scoring: Mapped[str | None]
    season: Mapped[int | None]
    week: Mapped[int | None]
    rank: Mapped[int | None]
    ecr: Mapped[float | None]
    pos_rank: Mapped[str | None]
    sd: Mapped[float | None]
    best: Mapped[int | None]
    worst: Mapped[int | None]
    fetched_at: Mapped[datetime] = mapped_column(default=utc_now)


class DataLoad(Base):
    """One finished populate_stats run. Its id changes whenever data is reloaded, which lets
    caches notice reloads that keep row counts the same (e.g. stat corrections)."""

    __tablename__ = "data_loads"

    id: Mapped[int] = mapped_column(primary_key=True)
    seasons: Mapped[str]                       # e.g. "2025,2026"
    finished_at: Mapped[datetime] = mapped_column(default=utc_now)


class NflGame(Base):
    """One game from the nflverse schedule, including the closing Vegas spread and total.

    spread_line is the home team's expected margin (positive = home favored).
    """

    __tablename__ = "nfl_games"
    __table_args__ = (Index("ix_nfl_games_season_week", "season", "week"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[str] = mapped_column(unique=True)
    season: Mapped[int]
    week: Mapped[int]
    game_type: Mapped[str]
    gameday: Mapped[str | None]
    home_team: Mapped[str]
    away_team: Mapped[str]
    home_score: Mapped[int | None]
    away_score: Mapped[int | None]
    spread_line: Mapped[float | None]
    total_line: Mapped[float | None]


class WeeklySourceProjection(Base):
    """One player's line from an outside weekly source (Vegas props, Yahoo experts, ESPN).

    Rows for a (season, week, source) are replaced on each refresh. `stats` holds projected
    raw stats as JSON (scored locally, so any scoring format works); `rank` holds a
    positional expert rank. `expert` names the ranker for expert sources.
    """

    __tablename__ = "weekly_source_projections"
    __table_args__ = (Index("ix_weekly_source_projections_lookup", "season", "week", "source", "scoring"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int]
    week: Mapped[int]
    source: Mapped[str]                        # "vegas_props", "yahoo_experts", "espn"
    scoring: Mapped[str | None]                # set for rank sources; None when stats are raw
    expert: Mapped[str | None]
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    player_name: Mapped[str]
    position: Mapped[str | None]
    team: Mapped[str | None]
    opponent: Mapped[str | None]
    rank: Mapped[float | None]
    stats: Mapped[str | None]
    fetched_at: Mapped[datetime] = mapped_column(default=utc_now)


class ExternalApiCall(Base):
    """One call to a rate-limited third-party API; persisted so quotas survive restarts."""

    __tablename__ = "external_api_calls"
    __table_args__ = (Index("ix_external_api_calls_source_called_at", "source", "called_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str]
    called_at: Mapped[datetime] = mapped_column(default=utc_now)
    ok: Mapped[bool]
