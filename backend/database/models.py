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
    __table_args__ = (Index("ix_players_gsis_id", "gsis_id", unique=True),
                      # Two Yahoo players can never map to one app player (NULLs are allowed).
                      Index("ix_players_yahoo_player_id", "yahoo_player_id", unique=True))
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
    gametime: Mapped[str | None] = mapped_column(default=None)     # "13:00", US Eastern
    roof: Mapped[str | None] = mapped_column(default=None)         # outdoors, dome, closed, open
    surface: Mapped[str | None] = mapped_column(default=None)
    temp: Mapped[float | None] = mapped_column(default=None)       # deg F at kickoff (filled after the game)
    wind: Mapped[float | None] = mapped_column(default=None)       # mph (filled after the game)
    stadium_id: Mapped[str | None] = mapped_column(default=None)


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
    pct_rostered: Mapped[float | None]         # ESPN league-wide roster rate, 0-100
    fetched_at: Mapped[datetime] = mapped_column(default=utc_now)


class InjuryReport(Base):
    """The official weekly injury report (nflverse): game status and final practice status."""

    __tablename__ = "injury_reports"
    __table_args__ = (Index("ix_injury_reports_season_week", "season", "week"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int]
    week: Mapped[int]
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    team: Mapped[str | None]
    report_status: Mapped[str | None]          # Out / Doubtful / Questionable / None
    practice_status: Mapped[str | None]        # DNP / LP / FP (normalized)
    injury: Mapped[str | None]


class DepthChartEntry(Base):
    """Latest depth chart per team for offensive skill positions (nflverse, ESPN-sourced)."""

    __tablename__ = "depth_charts"
    __table_args__ = (Index("ix_depth_charts_team", "team"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    team: Mapped[str]
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    position: Mapped[str]                      # QB / RB / WR / TE
    depth: Mapped[int]                         # 1 = starter
    as_of: Mapped[str | None]


class PlayerStatus(Base):
    """Live status from Sleeper (refreshed hourly): injury designation and depth order."""

    __tablename__ = "player_status"

    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), primary_key=True)
    injury_status: Mapped[str | None]          # Out, Doubtful, Questionable, IR, PUP, Sus, ...
    injury_body_part: Mapped[str | None]
    team: Mapped[str | None]
    depth_position: Mapped[str | None]
    depth_order: Mapped[int | None]
    fetched_at: Mapped[datetime] = mapped_column(default=utc_now)


class ExternalApiCall(Base):
    """One call to a rate-limited third-party API; persisted so quotas survive restarts."""

    __tablename__ = "external_api_calls"
    __table_args__ = (Index("ix_external_api_calls_source_called_at", "source", "called_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str]
    called_at: Mapped[datetime] = mapped_column(default=utc_now)
    ok: Mapped[bool]


class RankingsSnapshot(Base):
    """A computed week of rankings, saved so a restart doesn't redo the slow first build.

    Keyed by league settings, week and a hash of every input (source snapshots, live status,
    reports, stat loads, and the model code itself); any change makes a new key. The payload
    is a pickle written and read only by this app's local database (it is never committed).
    """

    __tablename__ = "rankings_snapshots"

    cache_key: Mapped[str] = mapped_column(primary_key=True)
    season: Mapped[int]
    week: Mapped[int]
    payload: Mapped[bytes]
    computed_at: Mapped[datetime] = mapped_column(default=utc_now)


class ProjectionHistory(Base):
    """The last pre-kickoff projection for a player-week, per scoring format.

    Kept so projections can be scored against what happened (python -m services.projection_eval)
    and source weights learned from the results.
    """

    __tablename__ = "projection_history"

    season: Mapped[int] = mapped_column(primary_key=True)
    week: Mapped[int] = mapped_column(primary_key=True)
    scoring: Mapped[str] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), primary_key=True)
    position: Mapped[str]
    projection: Mapped[float]
    if_active: Mapped[float]
    play_prob: Mapped[float]
    floor: Mapped[float | None]
    ceiling: Mapped[float | None]
    sources: Mapped[str]                       # JSON {source: points}
    computed_at: Mapped[datetime] = mapped_column(default=utc_now)


class LearnedWeights(Base):
    """Week X source weights after learning from one completed week (services/weight_learning.py)."""

    __tablename__ = "learned_weights"
    __table_args__ = (Index("ux_learned_weights_season_week", "season", "week", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int]
    week: Mapped[int]                          # the completed week this update learned from
    rows: Mapped[int]                          # scored player-weeks behind it
    weights: Mapped[str]                       # JSON {source: weight}, used by the rankings
    optimal: Mapped[str]                       # JSON: that week's best-fit weights (for reference)
    mae_before: Mapped[float]
    mae_after: Mapped[float]
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class TeamGameStats(Base):
    """One team's game: offense, defense, kicking and play-by-play situations (nflverse).

    Defensive numbers are the team's own (sacks it made, takeaways); what it allowed is the
    opponent's row for the same game. Red-zone trips are drives reaching the opponent 20.
    """

    __tablename__ = "team_game_stats"
    __table_args__ = (Index("ux_team_game_stats", "season", "week", "team", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int]
    week: Mapped[int]
    team: Mapped[str]
    opponent: Mapped[str]
    points_for: Mapped[int | None]
    points_against: Mapped[int | None]
    plays: Mapped[int]                      # dropbacks + designed runs
    dropbacks: Mapped[int]
    pass_epa: Mapped[float]
    rush_epa: Mapped[float]
    sacks_taken: Mapped[float]
    ints_thrown: Mapped[int]
    fumbles_lost: Mapped[int]
    def_sacks: Mapped[float]
    def_ints: Mapped[int]
    def_fumble_rec: Mapped[int]
    def_tds: Mapped[int]                    # defensive + special-teams return TDs
    def_safeties: Mapped[int]
    def_blocks: Mapped[int]
    rz_trips: Mapped[int]
    rz_tds: Mapped[int]
    fourth_downs: Mapped[int]               # 4th-down decisions (go, punt or field goal)
    fourth_down_goes: Mapped[int]
    fg_att: Mapped[int]
    fg_made: Mapped[int]
    pat_att: Mapped[int]
    pat_made: Mapped[int]


class KickerGame(Base):
    """A kicker's game: field goals made and missed by distance, and extra points."""

    __tablename__ = "kicker_games"
    __table_args__ = (Index("ix_kicker_games_player", "player_id", "season", "week"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int]
    week: Mapped[int]
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    team: Mapped[str | None]
    opponent: Mapped[str | None]
    made_0_39: Mapped[int]
    made_40_49: Mapped[int]
    made_50: Mapped[int]
    missed_0_39: Mapped[int]
    missed_40_49: Mapped[int]
    missed_50: Mapped[int]
    pat_made: Mapped[int]
    pat_att: Mapped[int]


class PlayerRedZone(Base):
    """A player's red-zone opportunities in one game (carries and targets inside the 20 and the 10)."""

    __tablename__ = "player_red_zone"
    __table_args__ = (Index("ix_player_red_zone", "season", "week", "player_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int]
    week: Mapped[int]
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    team: Mapped[str | None]
    rz_carries: Mapped[int]
    rz_targets: Mapped[int]
    i10_carries: Mapped[int]
    i10_targets: Mapped[int]


class GameForecast(Base):
    """Kickoff-hour weather forecast for an upcoming outdoor game (services/weather.py)."""

    __tablename__ = "game_forecasts"

    game_id: Mapped[str] = mapped_column(primary_key=True)
    temp: Mapped[float | None]
    wind: Mapped[float | None]
    fetched_at: Mapped[datetime] = mapped_column(default=utc_now)


class AppState(Base):
    """Small JSON values the app keeps between restarts: background job status, Odds API
    credits left, the last backup (services/system_status.py)."""

    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str]                         # JSON
    updated_at: Mapped[datetime] = mapped_column(default=utc_now)
