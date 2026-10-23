"""external api call log (persists FantasyPros quota usage)

Revision ID: 9c3e5a7b1d2f
Revises: 5b8d2e6f0a14
Create Date: 2026-10-22 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c3e5a7b1d2f'
down_revision: Union[str, Sequence[str], None] = '5b8d2e6f0a14'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "external_api_calls"
INDEX = "ix_external_api_calls_source_called_at"


def upgrade() -> None:
    """Upgrade schema."""
    # Skip when Base.metadata.create_all() already built the table.
    if sa.inspect(op.get_bind()).has_table(TABLE):
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("called_at", sa.DateTime(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
    )
    op.create_index(INDEX, TABLE, ["source", "called_at"])


def downgrade() -> None:
    """Downgrade schema."""
    if sa.inspect(op.get_bind()).has_table(TABLE):
        op.drop_table(TABLE)   # drops its index too
