"""index player_stats_weekly by season and week

Revision ID: f7b9d1e3a5c7
Revises: e6a8c0d2f4b6
Create Date: 2026-10-30 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7b9d1e3a5c7'
down_revision: Union[str, Sequence[str], None] = 'e6a8c0d2f4b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    if "ix_player_stats_weekly_season_week" not in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(
            "player_stats_weekly")}:
        op.create_index("ix_player_stats_weekly_season_week", "player_stats_weekly", ["season", "week"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_player_stats_weekly_season_week", table_name="player_stats_weekly")
