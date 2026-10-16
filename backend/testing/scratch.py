from sqlalchemy import select, func
from database.db import engine
from database.models import (
    PlayerStatsWeekly,
    PlayerPassingAdvancedWeekly,
    PlayerReceivingAdvancedWeekly,
    PlayerRushingAdvancedWeekly,
)
from sqlalchemy.orm import Session

tables = {
    "player_stats_weekly": PlayerStatsWeekly,
    "player_passing_advanced_weekly": PlayerPassingAdvancedWeekly,
    "player_receiving_advanced_weekly": PlayerReceivingAdvancedWeekly,
    "player_rushing_advanced_weekly": PlayerRushingAdvancedWeekly,
}

with Session(engine) as session:
    for name, model in tables.items():
        row = session.execute(select(func.max(model.season), func.min(model.season))).first()
        print(f"{name}: seasons {row[1]} to {row[0]}")