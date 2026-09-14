from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from datetime import datetime

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(unique=True)
    hashed_password: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
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
    owner_display_name: Mapped[str | None]
    is_users_team: Mapped[bool] = mapped_column(default=0)
    
class Player(Base):
    __tablename__ = "players"
    id: Mapped[int] = mapped_column(primary_key=True)
    gsis_id: Mapped[str | None]
    yahoo_player_id: Mapped[str | None]
    sleeper_player_id: Mapped[str | None]
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

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    season: Mapped[int]
    week: Mapped[int]
    passing_yards: Mapped[int | None]
    rushing_yards: Mapped[int | None]
    receiving_yards: Mapped[int | None]
    receptions: Mapped[int | None]
    targets: Mapped[int | None]
    touchdowns: Mapped[int | None]


class PlayerAdvancedStatsWeekly(Base):
    __tablename__ = "player_advanced_stats_weekly"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    season: Mapped[int]
    week: Mapped[int]
    avg_separation: Mapped[float | None]
    time_to_throw: Mapped[float | None]
    rush_yards_over_expected: Mapped[float | None]


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
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)