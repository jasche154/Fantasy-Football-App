"""replay injury tags

Revision ID: b9d3f5a7c2e4
Revises: b4d6f8a0c2e5
Create Date: 2026-11-20 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b9d3f5a7c2e4'
down_revision: Union[str, Sequence[str], None] = 'b4d6f8a0c2e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Guarded: Base.metadata.create_all() may already have built it.
    if not sa.inspect(op.get_bind()).has_table("replay_injuries"):
        op.create_table(
            "replay_injuries",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("season", sa.Integer(), nullable=False),
            sa.Column("week", sa.Integer(), nullable=False),
            sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=False),
            sa.Column("tag", sa.String(), nullable=False),
            sa.Column("practice", sa.String(), nullable=True),
            sa.Column("injury", sa.String(), nullable=True),
        )
        op.create_index("ux_replay_injuries_key", "replay_injuries", ["season", "week", "player_id"], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    if sa.inspect(op.get_bind()).has_table("replay_injuries"):
        op.drop_table("replay_injuries")
