"""Replay League: saved games plus the point-in-time data they run on (preseason ADP, pre-game projections)."""
from datetime import datetime

from sqlalchemy import ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base, utc_now


class ReplayLeague(Base):
    """One saved game. The whole league (rosters, picks, results, log) lives in `state` as JSON,
    so a week advances in one write (services/replay_state.py)."""

    __tablename__ = "replay_leagues"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    season: Mapped[int]
    seed: Mapped[int]                          # every CPU decision derives from it, so reloads never re-roll
    settings: Mapped[str]                      # JSON
    state: Mapped[str]                         # JSON
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now)


class ReplayAdp(Base):
    """Preseason average draft position for a past season (PPR, 12 teams), as drafted before week 1."""

    __tablename__ = "replay_adp"
    __table_args__ = (Index("ux_replay_adp_season_player", "season", "player_id", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int]
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    adp: Mapped[float]
    stdev: Mapped[float | None]
    source: Mapped[str]                        # "ffc" or "mfl"
    # The ADP source's team code. End-of-season, so hindsight for anyone who moved: replay_pool uses it
    # only for a drafted player who never plays.
    team: Mapped[str | None]


class ReplayProjection(Base):
    """A site's pre-game projection for a past player-week, scored in each format."""

    __tablename__ = "replay_projections"
    __table_args__ = (Index("ux_replay_projections_key", "season", "week", "player_id", "source", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int]
    week: Mapped[int]
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    source: Mapped[str]                        # "espn" or "sleeper"
    pts_ppr: Mapped[float]
    pts_half: Mapped[float]
    pts_std: Mapped[float]
