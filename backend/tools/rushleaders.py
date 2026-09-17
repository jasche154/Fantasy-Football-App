from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
from database.models import Player, PlayerRushingAdvancedWeekly
from pathlib import Path
from database.db import engine

# Whitelist: only metrics we've deliberately vetted can be requested.
# Maps a safe, model-facing name -> the actual SQLAlchemy column to average.
RUSHING_METRICS = {
    "rush_yards_over_expected": PlayerRushingAdvancedWeekly.rush_yards_over_expected_per_att,
    "rush_pct_over_expected": PlayerRushingAdvancedWeekly.rush_pct_over_expected,
    # add more here as you build features that need them
}


def get_rushing_leaders(metric: str, season: int, min_attempts: int = 50,
                        limit: int = 15, ascending: bool = False):
    if metric not in RUSHING_METRICS:
        raise ValueError(f"Unknown metric '{metric}'. Valid options: {list(RUSHING_METRICS)}")

    metric_column = RUSHING_METRICS[metric]
    avg_expression = func.avg(metric_column)

    order_expression = avg_expression if ascending else avg_expression.desc()

    with Session(engine) as session:
        statement = (
            select(
                Player.full_name,
                avg_expression,
            )
            .join(Player, Player.id == PlayerRushingAdvancedWeekly.player_id)
            .where(PlayerRushingAdvancedWeekly.season == season)
            .group_by(Player.id)
            .having(func.sum(PlayerRushingAdvancedWeekly.rush_attempts) >= min_attempts)
            .order_by(order_expression)
            .limit(limit)
        )
        results = session.execute(statement).all()

    return [{"player": name, metric: round(value, 2)} for name, value in results]


if __name__ == "__main__":
    for row in get_rushing_leaders("rush_yards_over_expected", season=2025, limit=15):
        print(row)