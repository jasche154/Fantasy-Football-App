"""add player_expected_points_weekly

Revision ID: 11919bb26697
Revises: d7b5c4e6f566
Create Date: 2026-10-07 10:33:27.249097

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '11919bb26697'
down_revision: Union[str, Sequence[str], None] = 'd7b5c4e6f566'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "player_expected_points_weekly"
STAT_PREFIXES = [
    "pass_completions", "receptions", "pass_yards_gained", "rec_yards_gained",
    "rush_yards_gained", "pass_touchdown", "rec_touchdown", "rush_touchdown",
    "pass_first_down", "rec_first_down", "rush_first_down", "pass_interception",
    "rec_interception", "pass_fantasy_points", "rec_fantasy_points", "rush_fantasy_points",
    "total_yards_gained", "total_touchdown", "total_first_down", "total_fantasy_points",
]
STAT_COLUMNS = [f"{prefix}_exp" for prefix in STAT_PREFIXES] + [f"{prefix}_diff" for prefix in STAT_PREFIXES]


def upgrade() -> None:
    """Upgrade schema."""
    # The original autogenerate came out empty because Base.metadata.create_all() had
    # already created the table. Create it here only when it is missing.
    if sa.inspect(op.get_bind()).has_table(TABLE):
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("week", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.String(), nullable=True),
        sa.Column("posteam", sa.String(), nullable=True),
        *[sa.Column(name, sa.Float(), nullable=True) for name in STAT_COLUMNS],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table(TABLE)
