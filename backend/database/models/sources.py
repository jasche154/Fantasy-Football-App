"""Outside data: expert ranks, schedules, weekly sources, injuries, depth charts, live status, API calls."""
from datetime import datetime

from sqlalchemy import ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base, utc_now


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
