"""weekly rankings: nfl_games, weekly_source_projections, players.espn_player_id

Revision ID: e4a1b7c9d2f6
Revises: 9c3e5a7b1d2f
Create Date: 2026-10-23 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e4a1b7c9d2f6'
down_revision: Union[str, Sequence[str], None] = '9c3e5a7b1d2f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

GAMES = "nfl_games"
SOURCES = "weekly_source_projections"


def upgrade() -> None:
    """Upgrade schema."""
    # Every step is guarded: Base.metadata.create_all() may already have built it.
    inspector = sa.inspect(op.get_bind())
    if "espn_player_id" not in {c["name"] for c in inspector.get_columns("players")}:
        op.add_column("players", sa.Column("espn_player_id", sa.String(), nullable=True))
    if not inspector.has_table(GAMES):
        op.create_table(
            GAMES,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("game_id", sa.String(), nullable=False, unique=True),
            sa.Column("season", sa.Integer(), nullable=False),
            sa.Column("week", sa.Integer(), nullable=False),
            sa.Column("game_type", sa.String(), nullable=False),
            sa.Column("gameday", sa.String(), nullable=True),
            sa.Column("home_team", sa.String(), nullable=False),
            sa.Column("away_team", sa.String(), nullable=False),
            sa.Column("home_score", sa.Integer(), nullable=True),
            sa.Column("away_score", sa.Integer(), nullable=True),
            sa.Column("spread_line", sa.Float(), nullable=True),
            sa.Column("total_line", sa.Float(), nullable=True),
        )
        op.create_index("ix_nfl_games_season_week", GAMES, ["season", "week"])
    if not inspector.has_table(SOURCES):
        op.create_table(
            SOURCES,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("season", sa.Integer(), nullable=False),
            sa.Column("week", sa.Integer(), nullable=False),
            sa.Column("source", sa.String(), nullable=False),
            sa.Column("scoring", sa.String(), nullable=True),
            sa.Column("expert", sa.String(), nullable=True),
            sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=True),
            sa.Column("player_name", sa.String(), nullable=False),
            sa.Column("position", sa.String(), nullable=True),
            sa.Column("team", sa.String(), nullable=True),
            sa.Column("opponent", sa.String(), nullable=True),
            sa.Column("rank", sa.Float(), nullable=True),
            sa.Column("stats", sa.String(), nullable=True),
            sa.Column("fetched_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_weekly_source_projections_lookup", SOURCES,
                        ["season", "week", "source", "scoring"])


def downgrade() -> None:
    """Downgrade schema."""
    inspector = sa.inspect(op.get_bind())
    for table in (SOURCES, GAMES):
        if inspector.has_table(table):
            op.drop_table(table)
    if "espn_player_id" in {c["name"] for c in inspector.get_columns("players")}:
        with op.batch_alter_table("players") as batch:
            batch.drop_column("espn_player_id")
