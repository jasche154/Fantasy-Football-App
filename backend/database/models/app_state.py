"""What the app computes and keeps: snapshots, projection history, learned weights, team and kicker stats, red-zone usage, forecasts, app state."""
from datetime import datetime

from sqlalchemy import ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base, utc_now


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
