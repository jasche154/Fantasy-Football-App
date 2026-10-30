"""app state table and a unique Yahoo player id

Revision ID: e6a8c0d2f4b6
Revises: d5f7a9b1c3e4
Create Date: 2026-10-30 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e6a8c0d2f4b6'
down_revision: Union[str, Sequence[str], None] = 'd5f7a9b1c3e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("app_state"):
        op.create_table(
            "app_state",
            sa.Column("key", sa.String(), primary_key=True),
            sa.Column("value", sa.String(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    if "ix_players_yahoo_player_id" not in {i["name"] for i in inspector.get_indexes("players")}:
        duplicates = op.get_bind().execute(sa.text(
            "SELECT yahoo_player_id FROM players WHERE yahoo_player_id IS NOT NULL "
            "GROUP BY yahoo_player_id HAVING COUNT(*) > 1")).fetchall()
        if duplicates:
            raise RuntimeError(f"Players share Yahoo ids {[d[0] for d in duplicates][:10]}; "
                               "clear the wrong ones before upgrading.")
        op.create_index("ix_players_yahoo_player_id", "players", ["yahoo_player_id"], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_players_yahoo_player_id", table_name="players")
    op.drop_table("app_state")
