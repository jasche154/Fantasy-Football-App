"""replay_adp format column (ppr | half | std | 2qb)

Revision ID: d3f5a7b9c1e4
Revises: c1e3a5b7d9f2
Create Date: 2026-11-22 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3f5a7b9c1e4'
down_revision: Union[str, Sequence[str], None] = 'c1e3a5b7d9f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_INDEX, NEW_INDEX = "ux_replay_adp_season_player", "ux_replay_adp_season_player_format"


def _columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("replay_adp")}


def _indexes() -> set[str]:
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("replay_adp")}


def upgrade() -> None:
    """Upgrade schema. Every row stored so far is 12-team PPR ADP, so it is backfilled "ppr"."""
    if "format" not in _columns():              # Base.metadata.create_all() may already have added it
        op.add_column("replay_adp", sa.Column("format", sa.String(), nullable=False, server_default="ppr"))
    if OLD_INDEX in _indexes():
        op.drop_index(OLD_INDEX, table_name="replay_adp")
    if NEW_INDEX not in _indexes():
        op.create_index(NEW_INDEX, "replay_adp", ["season", "player_id", "format"], unique=True)


def downgrade() -> None:
    """Downgrade schema: only the PPR rows fit the old one-ADP-per-player table."""
    if "format" not in _columns():
        return
    op.execute("DELETE FROM replay_adp WHERE format != 'ppr'")
    if NEW_INDEX in _indexes():
        op.drop_index(NEW_INDEX, table_name="replay_adp")
    with op.batch_alter_table("replay_adp") as batch:
        batch.drop_column("format")
    if OLD_INDEX not in _indexes():
        op.create_index(OLD_INDEX, "replay_adp", ["season", "player_id"], unique=True)
