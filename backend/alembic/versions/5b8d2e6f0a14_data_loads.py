"""data load stamps (lets caches detect reloads)

Revision ID: 5b8d2e6f0a14
Revises: 4f2a9c1e7b3d
Create Date: 2026-10-22 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5b8d2e6f0a14'
down_revision: Union[str, Sequence[str], None] = '4f2a9c1e7b3d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "data_loads"


def upgrade() -> None:
    """Upgrade schema."""
    # Skip when Base.metadata.create_all() already built the table.
    if sa.inspect(op.get_bind()).has_table(TABLE):
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("seasons", sa.String(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    if sa.inspect(op.get_bind()).has_table(TABLE):
        op.drop_table(TABLE)
