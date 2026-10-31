"""Sleeper injury notes and news timestamp on player_status; injury_news signals table

Revision ID: a3c5e7f9b1d2
Revises: f7b9d1e3a5c7
Create Date: 2026-10-31 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3c5e7f9b1d2'
down_revision: Union[str, Sequence[str], None] = 'f7b9d1e3a5c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("player_status")}
    with op.batch_alter_table("player_status") as batch:
        if "injury_notes" not in columns:
            batch.add_column(sa.Column("injury_notes", sa.String(), nullable=True))
        if "news_updated" not in columns:      # milliseconds since 1970; SQLite INTEGER holds 64 bits
            batch.add_column(sa.Column("news_updated", sa.Integer(), nullable=True))
    if not inspector.has_table("injury_news"):
        op.create_table(
            "injury_news",
            sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), primary_key=True),
            sa.Column("news_updated", sa.Integer(), nullable=True),
            sa.Column("signals", sa.String(), nullable=False),
            sa.Column("checked_at", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    """Downgrade schema."""
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("injury_news"):
        op.drop_table("injury_news")
    columns = {c["name"] for c in inspector.get_columns("player_status")}
    with op.batch_alter_table("player_status") as batch:
        for name in ("news_updated", "injury_notes"):
            if name in columns:
                batch.drop_column(name)
