"""injury reports, depth charts, live player status, roster rate

Revision ID: f5b3c8d1e9a7
Revises: e4a1b7c9d2f6
Create Date: 2026-10-23 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f5b3c8d1e9a7'
down_revision: Union[str, Sequence[str], None] = 'e4a1b7c9d2f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Every step is guarded: Base.metadata.create_all() may already have built it.
    inspector = sa.inspect(op.get_bind())
    if "pct_rostered" not in {c["name"] for c in inspector.get_columns("weekly_source_projections")}:
        op.add_column("weekly_source_projections", sa.Column("pct_rostered", sa.Float(), nullable=True))
    if not inspector.has_table("injury_reports"):
        op.create_table(
            "injury_reports",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("season", sa.Integer(), nullable=False),
            sa.Column("week", sa.Integer(), nullable=False),
            sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=False),
            sa.Column("team", sa.String(), nullable=True),
            sa.Column("report_status", sa.String(), nullable=True),
            sa.Column("practice_status", sa.String(), nullable=True),
            sa.Column("injury", sa.String(), nullable=True),
        )
        op.create_index("ix_injury_reports_season_week", "injury_reports", ["season", "week"])
    if not inspector.has_table("depth_charts"):
        op.create_table(
            "depth_charts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("team", sa.String(), nullable=False),
            sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=False),
            sa.Column("position", sa.String(), nullable=False),
            sa.Column("depth", sa.Integer(), nullable=False),
            sa.Column("as_of", sa.String(), nullable=True),
        )
        op.create_index("ix_depth_charts_team", "depth_charts", ["team"])
    if not inspector.has_table("player_status"):
        op.create_table(
            "player_status",
            sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), primary_key=True),
            sa.Column("injury_status", sa.String(), nullable=True),
            sa.Column("injury_body_part", sa.String(), nullable=True),
            sa.Column("team", sa.String(), nullable=True),
            sa.Column("depth_position", sa.String(), nullable=True),
            sa.Column("depth_order", sa.Integer(), nullable=True),
            sa.Column("fetched_at", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    """Downgrade schema."""
    inspector = sa.inspect(op.get_bind())
    for table in ("player_status", "depth_charts", "injury_reports"):
        if inspector.has_table(table):
            op.drop_table(table)
    if "pct_rostered" in {c["name"] for c in inspector.get_columns("weekly_source_projections")}:
        with op.batch_alter_table("weekly_source_projections") as batch:
            batch.drop_column("pct_rostered")
