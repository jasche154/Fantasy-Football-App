"""kickoff weather forecasts

Revision ID: d5f7a9b1c3e4
Revises: c4e6f8a0b2d3
Create Date: 2026-10-30 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5f7a9b1c3e4'
down_revision: Union[str, Sequence[str], None] = 'c4e6f8a0b2d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    if not sa.inspect(op.get_bind()).has_table("game_forecasts"):
        op.create_table(
            "game_forecasts",
            sa.Column("game_id", sa.String(), primary_key=True),
            sa.Column("temp", sa.Float(), nullable=True),
            sa.Column("wind", sa.Float(), nullable=True),
            sa.Column("fetched_at", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("game_forecasts")
