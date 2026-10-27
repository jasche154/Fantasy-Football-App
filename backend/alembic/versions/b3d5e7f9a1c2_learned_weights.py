"""learned Week X source weights

Revision ID: b3d5e7f9a1c2
Revises: a8c2d4e6f1b3
Create Date: 2026-10-27 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3d5e7f9a1c2'
down_revision: Union[str, Sequence[str], None] = 'a8c2d4e6f1b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Guarded: the app also creates this table on first use.
    if not sa.inspect(op.get_bind()).has_table("learned_weights"):
        op.create_table(
            "learned_weights",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("season", sa.Integer(), nullable=False),
            sa.Column("week", sa.Integer(), nullable=False),
            sa.Column("rows", sa.Integer(), nullable=False),
            sa.Column("weights", sa.String(), nullable=False),
            sa.Column("optimal", sa.String(), nullable=False),
            sa.Column("mae_before", sa.Float(), nullable=False),
            sa.Column("mae_after", sa.Float(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ux_learned_weights_season_week", "learned_weights", ["season", "week"], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ux_learned_weights_season_week", table_name="learned_weights")
    op.drop_table("learned_weights")
