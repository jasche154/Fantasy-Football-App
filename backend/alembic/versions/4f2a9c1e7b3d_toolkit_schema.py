"""toolkit schema: crosswalk ids, indexes, snap counts, expert rankings

Revision ID: 4f2a9c1e7b3d
Revises: 11919bb26697
Create Date: 2026-10-22 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4f2a9c1e7b3d'
down_revision: Union[str, Sequence[str], None] = '11919bb26697'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

WEEKLY_TABLES = [
    "player_stats_weekly",
    "player_passing_advanced_weekly",
    "player_receiving_advanced_weekly",
    "player_rushing_advanced_weekly",
    "player_expected_points_weekly",
]


# Every step checks first, so this runs cleanly against databases that were partly built
# by Base.metadata.create_all() as well as ones built purely by migrations.
def _has_table(table: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table)


def _has_column(table: str, column: str) -> bool:
    return any(c["name"] == column for c in sa.inspect(op.get_bind()).get_columns(table))


def _has_index(table: str, index: str) -> bool:
    return any(i["name"] == index for i in sa.inspect(op.get_bind()).get_indexes(table))


def _create_snap_counts() -> None:
    op.create_table(
        "player_snap_counts_weekly",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("week", sa.Integer(), nullable=False),
        sa.Column("game_type", sa.String(), nullable=True),
        sa.Column("team", sa.String(), nullable=True),
        sa.Column("opponent", sa.String(), nullable=True),
        sa.Column("offense_snaps", sa.Integer(), nullable=True),
        sa.Column("offense_pct", sa.Float(), nullable=True),
    )
    op.create_index(
        "ix_player_snap_counts_weekly_player_season_week",
        "player_snap_counts_weekly",
        ["player_id", "season", "week"],
    )


def _create_expert_rankings() -> None:
    op.create_table(
        "expert_rankings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=True),
        sa.Column("fantasypros_id", sa.String(), nullable=True),
        sa.Column("player_name", sa.String(), nullable=False),
        sa.Column("position", sa.String(), nullable=True),
        sa.Column("team", sa.String(), nullable=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("ranking_type", sa.String(), nullable=False),
        sa.Column("scoring", sa.String(), nullable=True),
        sa.Column("season", sa.Integer(), nullable=True),
        sa.Column("week", sa.Integer(), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("ecr", sa.Float(), nullable=True),
        sa.Column("pos_rank", sa.String(), nullable=True),
        sa.Column("sd", sa.Float(), nullable=True),
        sa.Column("best", sa.Integer(), nullable=True),
        sa.Column("worst", sa.Integer(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
    )


def upgrade() -> None:
    """Upgrade schema."""
    if not _has_column("players", "fantasypros_id"):
        op.add_column("players", sa.Column("fantasypros_id", sa.String(), nullable=True))
    if not _has_column("teams", "name"):
        op.add_column("teams", sa.Column("name", sa.String(), nullable=True))
    if not _has_index("players", "ix_players_gsis_id"):
        op.create_index("ix_players_gsis_id", "players", ["gsis_id"], unique=True)

    for table in WEEKLY_TABLES:
        index = f"ix_{table}_player_season_week"
        if _has_table(table) and not _has_index(table, index):
            op.create_index(index, table, ["player_id", "season", "week"])

    if not _has_table("player_snap_counts_weekly"):
        _create_snap_counts()
    if not _has_table("expert_rankings"):
        _create_expert_rankings()


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("expert_rankings")
    op.drop_index("ix_player_snap_counts_weekly_player_season_week", "player_snap_counts_weekly")
    op.drop_table("player_snap_counts_weekly")
    for table in WEEKLY_TABLES:
        op.drop_index(f"ix_{table}_player_season_week", table)
    op.drop_index("ix_players_gsis_id", "players")
    with op.batch_alter_table("teams") as batch:
        batch.drop_column("name")
    with op.batch_alter_table("players") as batch:
        batch.drop_column("fantasypros_id")
