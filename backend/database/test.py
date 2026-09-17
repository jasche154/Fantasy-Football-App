from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
from models import Player, PlayerRushingAdvancedWeekly
import matplotlib.pyplot as plt
engine = create_engine("sqlite:///fantasy.db")

with Session(engine) as session:
    statement = (
        select(
            Player.full_name,
            func.avg(PlayerRushingAdvancedWeekly.rush_yards_over_expected_per_att),
            func.avg(PlayerRushingAdvancedWeekly.rush_pct_over_expected),
        )
        .join(Player, Player.id == PlayerRushingAdvancedWeekly.player_id)
        .where(PlayerRushingAdvancedWeekly.season == 2025)
        .group_by(Player.id)
        .having(func.sum(PlayerRushingAdvancedWeekly.rush_attempts) >= 50)
        .order_by(func.avg(PlayerRushingAdvancedWeekly.rush_yards_over_expected_per_att).desc())
        .limit(30)
    )

    results = session.execute(statement).all()
    for row in results:
        name, avg_yards_over_expected, avg_pct_over_expected = row
        print(name, round(avg_yards_over_expected, 2), round(avg_pct_over_expected, 2))




# names = [row[0] for row in results]
# avg_yoe = [row[1] for row in results]

# plt.figure(figsize=(10, 6))
# plt.barh(names, avg_yoe)
# plt.xlabel("Avg Rush Yards Over Expected per Attempt")
# plt.title("Top 15 RBs by Rushing Efficiency (2025, min. 50 attempts)")
# plt.gca().invert_yaxis()  # highest value at the top instead of bottom
# plt.tight_layout()
# plt.show()