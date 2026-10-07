from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
from database.models import Player, PlayerRushingAdvancedWeekly
from pathlib import Path
from database.db import engine
from difflib import get_close_matches

# Whitelist: only metrics we've deliberately vetted can be requested.
# Maps a safe, model-facing name -> the actual SQLAlchemy column to average.
RUSHING_METRICS = {
    "rush_yards_over_expected": PlayerRushingAdvancedWeekly.rush_yards_over_expected_per_att,
    "rush_pct_over_expected": PlayerRushingAdvancedWeekly.rush_pct_over_expected,
    # add more here as you build features that need them
}


def get_rushing_leaders(metric: str, season: int, min_attempts: int = 50,
                        limit: int = 15, ascending: bool = False, player_name: str | None = None):
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
            
        )
        results = session.execute(statement).all()
        if player_name is None:
            statement = statement.limit(limit)

        results = session.execute(statement).all()

        if player_name is None:
            return [{"player": name, metric: round(value, 2)} for name, value in results]
        else:
            # Try exact match first
            for rank, (name, value) in enumerate(results, start=1):
                if name.lower() == player_name.lower():
                    return {"rank": rank, "player": name, metric: round(value, 2)}

            # Fallback: fuzzy match if exact match found nothing
            all_names = [name for name, _ in results]
            close = get_close_matches(player_name, all_names, n=1, cutoff=0.7)

            if close:
                matched_name = close[0]
                for rank, (name, value) in enumerate(results, start=1):
                    if name == matched_name:
                        return {"rank": rank, "player": matched_name, metric: round(value, 2),
                                "note": f"No exact match for '{player_name}' — showing closest match: {matched_name}"}

            # Nothing worked, even fuzzy
            return {"player": player_name, "error": f"{player_name} not found — check spelling, or they may not meet the minimum attempts threshold."}
if __name__ == "__main__":
    for row in get_rushing_leaders("rush_yards_over_expected", season=2025, limit=15):
        print(row)