"""How the projections have done: live accuracy, backtest results, learned weights."""
import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.db import get_session
from database.models import LearnedWeights
from services import projection_eval, rankings_store
from services.projection_factors import FACTORS_PATH
from services.projections import latest_season
from services.weight_learning import DEFAULT_WEIGHTS

router = APIRouter(prefix="/api/model")


@router.get("/accuracy")
def accuracy(scoring: str = Query("ppr", pattern="^(ppr|half|std)$"), session: Session = Depends(get_session)) -> dict:
    season = latest_season(session)
    live = (projection_eval.evaluate(rankings_store.history(session, season, scoring),
                                     projection_eval.actual_points(session, season, scoring)) if season else {"n": 0})
    try:
        factors = json.loads(FACTORS_PATH.read_text(encoding="utf-8"))
        backtest = {"seasons": factors.get("seasons"), "held_out": factors.get("held_out")}
    except (OSError, ValueError):
        backtest = None
    rows = session.scalars(select(LearnedWeights).order_by(LearnedWeights.season, LearnedWeights.week)).all()
    history = []
    for row in rows:
        weights = json.loads(row.weights)
        history.append({"season": row.season, "week": row.week, "rows": row.rows,
                        "weights": {k: weights.get(k) for k in DEFAULT_WEIGHTS},
                        "by_position": weights.get("_by_position") or {},
                        "mae_before": row.mae_before, "mae_after": row.mae_after})
    return {"season": season, "scoring": scoring, "live": live, "backtest": backtest,
            "starting_weights": DEFAULT_WEIGHTS, "weight_history": history}
