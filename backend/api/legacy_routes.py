"""The original rushing-rankings endpoint, kept for existing clients."""
from fastapi import APIRouter, HTTPException, Query

from tools.rushleaders import RUSHING_METRICS, get_rushing_leaders

router = APIRouter(prefix="/api")


@router.get("/rankings/rushing")
def get_rushing_rankings(
    metric: str = "rush_yards_over_expected",
    season: int = Query(2025, ge=1999, le=2100),
    limit: int = Query(15, ge=1, le=200),
    min_attempts: int = Query(50, ge=0, le=1000),
    ascending: bool = False,
) -> dict:
    if metric not in RUSHING_METRICS:
        raise HTTPException(422, f"Unknown metric '{metric}'. Valid options: {list(RUSHING_METRICS)}")
    results = get_rushing_leaders(metric=metric, season=season, limit=limit,
                                  min_attempts=min_attempts, ascending=ascending)
    return {"metric": metric, "season": season, "results": results}
