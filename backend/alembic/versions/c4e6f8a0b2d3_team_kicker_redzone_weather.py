"""team game stats, kicker games, red-zone usage, game weather

Revision ID: c4e6f8a0b2d3
Revises: b3d5e7f9a1c2
Create Date: 2026-10-30 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4e6f8a0b2d3'
down_revision: Union[str, Sequence[str], None] = 'b3d5e7f9a1c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

GAME_COLUMNS = (("gametime", sa.String()), ("roof", sa.String()), ("surface", sa.String()),
                ("temp", sa.Float()), ("wind", sa.Float()), ("stadium_id", sa.String()))
INT = sa.Integer()


def _ints(*names: str) -> list[sa.Column]:
    return [sa.Column(name, sa.Integer(), nullable=False) for name in names]


def upgrade() -> None:
    """Upgrade schema."""
    # Guarded: Base.metadata.create_all() may already have built these.
    inspector = sa.inspect(op.get_bind())
    existing = {c["name"] for c in inspector.get_columns("nfl_games")}
    for name, kind in GAME_COLUMNS:
        if name not in existing:
            op.add_column("nfl_games", sa.Column(name, kind, nullable=True))
    if not inspector.has_table("team_game_stats"):
        op.create_table(
            "team_game_stats",
            sa.Column("id", sa.Integer(), primary_key=True),
            *_ints("season", "week"),
            sa.Column("team", sa.String(), nullable=False),
            sa.Column("opponent", sa.String(), nullable=False),
            sa.Column("points_for", sa.Integer(), nullable=True),
            sa.Column("points_against", sa.Integer(), nullable=True),
            *_ints("plays", "dropbacks"),
            sa.Column("pass_epa", sa.Float(), nullable=False),
            sa.Column("rush_epa", sa.Float(), nullable=False),
            sa.Column("sacks_taken", sa.Float(), nullable=False),
            *_ints("ints_thrown", "fumbles_lost"),
            sa.Column("def_sacks", sa.Float(), nullable=False),
            *_ints("def_ints", "def_fumble_rec", "def_tds", "def_safeties", "def_blocks", "rz_trips", "rz_tds",
                   "fourth_downs", "fourth_down_goes", "fg_att", "fg_made", "pat_att", "pat_made"),
        )
        op.create_index("ux_team_game_stats", "team_game_stats", ["season", "week", "team"], unique=True)
    if not inspector.has_table("kicker_games"):
        op.create_table(
            "kicker_games",
            sa.Column("id", sa.Integer(), primary_key=True),
            *_ints("season", "week"),
            sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=False),
            sa.Column("team", sa.String(), nullable=True),
            sa.Column("opponent", sa.String(), nullable=True),
            *_ints("made_0_39", "made_40_49", "made_50", "missed_0_39", "missed_40_49", "missed_50",
                   "pat_made", "pat_att"),
        )
        op.create_index("ix_kicker_games_player", "kicker_games", ["player_id", "season", "week"])
    if not inspector.has_table("player_red_zone"):
        op.create_table(
            "player_red_zone",
            sa.Column("id", sa.Integer(), primary_key=True),
            *_ints("season", "week"),
            sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=False),
            sa.Column("team", sa.String(), nullable=True),
            *_ints("rz_carries", "rz_targets", "i10_carries", "i10_targets"),
        )
        op.create_index("ix_player_red_zone", "player_red_zone", ["season", "week", "player_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_player_red_zone", table_name="player_red_zone")
    op.drop_table("player_red_zone")
    op.drop_index("ix_kicker_games_player", table_name="kicker_games")
    op.drop_table("kicker_games")
    op.drop_index("ux_team_game_stats", table_name="team_game_stats")
    op.drop_table("team_game_stats")
    with op.batch_alter_table("nfl_games") as batch:
        for name, _ in GAME_COLUMNS:
            batch.drop_column(name)
