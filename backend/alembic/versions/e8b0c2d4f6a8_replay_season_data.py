"""replay season data (a simulated league's frozen season)

Revision ID: e8b0c2d4f6a8
Revises: d3f5a7b9c1e4
Create Date: 2026-11-23 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8b0c2d4f6a8'
down_revision: Union[str, Sequence[str], None] = 'd3f5a7b9c1e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Guarded: Base.metadata.create_all() may already have built it.
    if not sa.inspect(op.get_bind()).has_table("replay_season_data"):
        op.create_table(
            "replay_season_data",
            sa.Column("league_id", sa.Integer(), sa.ForeignKey("replay_leagues.id", ondelete="CASCADE"),
                      primary_key=True),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("known_through", sa.Integer(), nullable=False),
            sa.Column("blob", sa.LargeBinary(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    """Downgrade schema."""
    if sa.inspect(op.get_bind()).has_table("replay_season_data"):
        op.drop_table("replay_season_data")
