"""Users, Yahoo connections, leagues, teams, players and rosters."""
from datetime import datetime

from sqlalchemy import ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base, utc_now


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
