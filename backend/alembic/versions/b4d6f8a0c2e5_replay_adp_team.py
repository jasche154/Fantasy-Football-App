"""replay_adp team column

Revision ID: b4d6f8a0c2e5
Revises: a8c2e4f6b1d3
Create Date: 2026-11-20 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4d6f8a0c2e5'
down_revision: Union[str, Sequence[str], None] = 'a8c2e4f6b1d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("replay_adp")}


def upgrade() -> None:
    """Upgrade schema."""
    if "team" not in _columns():                # Base.metadata.create_all() may already have added it
        op.add_column("replay_adp", sa.Column("team", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    if "team" in _columns():
        with op.batch_alter_table("replay_adp") as batch:
            batch.drop_column("team")
