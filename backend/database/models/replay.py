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


class ReplaySeasonData(Base):
    """A simulated league's whole season (real weeks + its own simulated future), frozen when it was created
    (services/replay_blob.py), so new real data never changes a saved game. Its own table: listing games
    never loads a blob."""

    __tablename__ = "replay_season_data"

    league_id: Mapped[int] = mapped_column(ForeignKey("replay_leagues.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str]                          # "live" | "future"
    known_through: Mapped[int]                 # the last real week in it (0: a future season)
    blob: Mapped[bytes]                        # replay_blob.dumps
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class ReplayAdp(Base):
    """Preseason average draft position for a past season (12 teams) in one draft format, as drafted before week 1."""

    __tablename__ = "replay_adp"
    __table_args__ = (Index("ux_replay_adp_season_player_format", "season", "player_id", "format", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int]
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    # "ppr" | "half" | "std" | "2qb" (2-QB/superflex drafts, PPR); rows from before formats existed are "ppr"
    format: Mapped[str] = mapped_column(default="ppr", server_default="ppr")
    adp: Mapped[float]
    stdev: Mapped[float | None]
    source: Mapped[str]                        # "ffc", "ffc_borrowed" (another format's FFC list) or "mfl"
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


class ReplayInjury(Base):
    """A player's injury tag for one past week: the official report (Q/D/O) or his roster status (IR/PUP/SUS/EXE)."""

    __tablename__ = "replay_injuries"
    __table_args__ = (Index("ux_replay_injuries_key", "season", "week", "player_id", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int]
    week: Mapped[int]
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    tag: Mapped[str]                           # Q | D | O | IR | PUP | SUS | EXE (commissioner exempt list)
    practice: Mapped[str | None]               # DNP | LP | FP
    injury: Mapped[str | None]


class ReplayExpertRank(Base):
    """A FantasyPros consensus position rank from one snapshot: the last preseason cheatsheet ("pre") or an
    in-season rest-of-season page ("ros"). services/replay_experts.py loads and reads them."""

    __tablename__ = "replay_expert_ranks"
    __table_args__ = (Index("ux_replay_expert_ranks_key", "season", "kind", "scrape_date", "player_id", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int]
    kind: Mapped[str]                          # "pre" | "ros"
    scrape_date: Mapped[str]                   # ISO date the page was captured
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    position: Mapped[str]
    pos_rank: Mapped[int]                      # 1-based order by ecr within the page and snapshot
    ecr: Mapped[float]
