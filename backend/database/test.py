from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
from models import Player, PlayerRushingAdvancedWeekly, PlayerStatsWeekly

engine = create_engine("sqlite:///fantasy.db")

with Session(engine) as session:
    statement = (
        select(
            Player.full_name,
            func.avg(PlayerRushingAdvancedWeekly.rush_yards_over_expected_per_att),
            func.avg(PlayerRushingAdvancedWeekly.rush_pct_over_expected),
        )
        .join(Player, Player.id == PlayerRushingAdvancedWeekly.player_id)
        .where(PlayerRushingAdvancedWeekly.season == 2025 and )
        .group_by(Player.id)
        .order_by(func.avg(PlayerRushingAdvancedWeekly.rush_yards_over_expected_per_att).desc())
        # TODO 1: add .order_by(...) to sort by the rush_yards_over_expected average, descending
        .limit(15)
        # TODO 2: add .limit(15) so you only get the top 15 rather than every player
    )

    results = session.execute(statement).all()
    for row in results:
        name, avg_yards_over_expected, avg_pct_over_expected = row
        print(name, round(avg_yards_over_expected,2), avg_pct_over_expected)
        pass