"""rankings snapshots and projection history

Revision ID: a8c2d4e6f1b3
Revises: f5b3c8d1e9a7
Create Date: 2026-10-24 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a8c2d4e6f1b3'
down_revision: Union[str, Sequence[str], None] = 'f5b3c8d1e9a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Guarded: the app also creates these tables on first use.
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("rankings_snapshots"):
        op.create_table(
            "rankings_snapshots",
            sa.Column("cache_key", sa.String(), primary_key=True),
            sa.Column("season", sa.Integer(), nullable=False),
            sa.Column("week", sa.Integer(), nullable=False),
            sa.Column("payload", sa.LargeBinary(), nullable=False),
            sa.Column("computed_at", sa.DateTime(), nullable=False),
        )
    if not inspector.has_table("projection_history"):
        op.create_table(
            "projection_history",
            sa.Column("season", sa.Integer(), primary_key=True),
            sa.Column("week", sa.Integer(), primary_key=True),
            sa.Column("scoring", sa.String(), primary_key=True),
            sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), primary_key=True),
            sa.Column("position", sa.String(), nullable=False),
            sa.Column("projection", sa.Float(), nullable=False),
            sa.Column("if_active", sa.Float(), nullable=False),
            sa.Column("play_prob", sa.Float(), nullable=False),
            sa.Column("floor", sa.Float(), nullable=True),
            sa.Column("ceiling", sa.Float(), nullable=True),
            sa.Column("sources", sa.String(), nullable=False),
            sa.Column("computed_at", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("projection_history")
    op.drop_table("rankings_snapshots")
