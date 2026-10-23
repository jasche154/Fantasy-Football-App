from sqlalchemy.orm import Session

from database.db import engine
from services.leaderboards import leaderboard

# Whitelist: only metrics we've deliberately vetted can be requested.
# Maps a safe, model-facing name -> (leaderboard metric key, scale applied to its value).
# Values keep the scale this tool always returned: RYOE per attempt, and the share of
# carries beating expectation as a 0-1 fraction.
RUSHING_METRICS = {
    "rush_yards_over_expected": ("ryoe_per_att", 1.0),
    "rush_pct_over_expected": ("rush_pct_over_expected", 0.01),
}


def get_rushing_leaders(metric: str, season: int, min_attempts: int = 50,
                        limit: int = 15, ascending: bool = False, player_name: str | None = None):
    if metric not in RUSHING_METRICS:
        raise ValueError(f"Unknown metric '{metric}'. Valid options: {list(RUSHING_METRICS)}")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    metric_key, scale = RUSHING_METRICS[metric]

    with Session(engine) as session:
        rows = leaderboard(session, "rushing", metric_key, season, min_volume=min_attempts,
                           limit=None if player_name else limit, ascending=ascending)

    def value(row) -> float:
        return round(row.value * scale, 2)

    if player_name is None:
        return [{"player": row.name, metric: value(row)} for row in rows]

    exact = next((r for r in rows if r.name.lower() == player_name.lower()), None)
    if exact:
        return {"rank": exact.rank, "player": exact.name, metric: value(exact)}

    # Fallback: fuzzy match if exact match found nothing
    from difflib import get_close_matches
    close = get_close_matches(player_name, [r.name for r in rows], n=1, cutoff=0.7)
    if close:
        match = next(r for r in rows if r.name == close[0])
        return {"rank": match.rank, "player": match.name, metric: value(match),
                "note": f"No exact match for '{player_name}' — showing closest match: {match.name}"}

    # Nothing worked, even fuzzy
    return {"player": player_name, "error": f"{player_name} not found — check spelling, or they may not meet the minimum attempts threshold."}


if __name__ == "__main__":
    for row in get_rushing_leaders("rush_yards_over_expected", season=2025, limit=15):
        print(row)
