from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from tools.rushleaders import get_rushing_leaders, RUSHING_METRICS

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
def health_check():
    return {"status": "ok", "message": "Backend is alive"}

@app.get("/api/rankings/rushing")
def get_rushing_rankings(
    metric: str = "rush_yards_over_expected",
    season: int = 2025,
    limit: int = 15,
    min_attempts: int = 50,
    ascending: bool = False,
):
    if metric not in RUSHING_METRICS:
        return {"error": f"Unknown metric '{metric}'. Valid options: {list(RUSHING_METRICS.keys())}"}
    try:
        results = get_rushing_leaders(
            metric=metric,
            season=season,
            limit=limit,
            min_attempts=min_attempts,
            ascending=ascending,
        )
        return {"metric": metric, "season": season, "results": results}
    except Exception as e:
        return {"error": str(e)}