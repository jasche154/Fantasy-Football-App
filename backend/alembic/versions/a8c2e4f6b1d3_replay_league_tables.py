"""replay league tables

Revision ID: a8c2e4f6b1d3
Revises: a3c5e7f9b1d2
Create Date: 2026-11-20 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a8c2e4f6b1d3'
down_revision: Union[str, Sequence[str], None] = 'a3c5e7f9b1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Every step is guarded: Base.metadata.create_all() may already have built it.
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("replay_leagues"):
        op.create_table(
            "replay_leagues",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("season", sa.Integer(), nullable=False),
            sa.Column("seed", sa.Integer(), nullable=False),
            sa.Column("settings", sa.String(), nullable=False),
            sa.Column("state", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    if not inspector.has_table("replay_adp"):
        op.create_table(
            "replay_adp",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("season", sa.Integer(), nullable=False),
            sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=False),
            sa.Column("adp", sa.Float(), nullable=False),
            sa.Column("stdev", sa.Float(), nullable=True),
            sa.Column("source", sa.String(), nullable=False),
        )
        op.create_index("ux_replay_adp_season_player", "replay_adp", ["season", "player_id"], unique=True)
    if not inspector.has_table("replay_projections"):
        op.create_table(
            "replay_projections",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("season", sa.Integer(), nullable=False),
            sa.Column("week", sa.Integer(), nullable=False),
            sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=False),
            sa.Column("source", sa.String(), nullable=False),
            sa.Column("pts_ppr", sa.Float(), nullable=False),
            sa.Column("pts_half", sa.Float(), nullable=False),
            sa.Column("pts_std", sa.Float(), nullable=False),
        )
        op.create_index("ux_replay_projections_key", "replay_projections",
                        ["season", "week", "player_id", "source"], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    inspector = sa.inspect(op.get_bind())
    for table in ("replay_projections", "replay_adp", "replay_leagues"):
        if inspector.has_table(table):
            op.drop_table(table)
