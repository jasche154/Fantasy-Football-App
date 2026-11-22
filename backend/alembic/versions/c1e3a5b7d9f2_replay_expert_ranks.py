"""replay expert ranks

Revision ID: c1e3a5b7d9f2
Revises: b9d3f5a7c2e4
Create Date: 2026-11-21 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1e3a5b7d9f2'
down_revision: Union[str, Sequence[str], None] = 'b9d3f5a7c2e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Guarded: Base.metadata.create_all() may already have built it.
    if not sa.inspect(op.get_bind()).has_table("replay_expert_ranks"):
        op.create_table(
            "replay_expert_ranks",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("season", sa.Integer(), nullable=False),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("scrape_date", sa.String(), nullable=False),
            sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=False),
            sa.Column("position", sa.String(), nullable=False),
            sa.Column("pos_rank", sa.Integer(), nullable=False),
            sa.Column("ecr", sa.Float(), nullable=False),
        )
        op.create_index("ux_replay_expert_ranks_key", "replay_expert_ranks",
                        ["season", "kind", "scrape_date", "player_id"], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    if sa.inspect(op.get_bind()).has_table("replay_expert_ranks"):
        op.drop_table("replay_expert_ranks")
