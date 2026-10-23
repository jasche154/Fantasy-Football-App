"""Trade calculator and buy-low / sell-high endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from api.deps import LeagueBody, Position, league_params, valuation_or_503
from database.db import get_session
from services import serialize
from services.insights import find_insights
from services.league import LeagueSettings
from services.trade import evaluate_trade
from services.trade_finder import find_trades

router = APIRouter(prefix="/api")


class TradeRequest(BaseModel):
    give: list[int] = Field(..., min_length=1, max_length=10)
    get: list[int] = Field(..., min_length=1, max_length=10)
    settings: LeagueBody = LeagueBody()

    @field_validator("give", "get")
    @classmethod
    def no_duplicates(cls, ids: list[int]) -> list[int]:
        if len(set(ids)) != len(ids):
            raise ValueError("a player is listed more than once on the same side")
        return ids


@router.post("/trade/evaluate")
def trade_evaluate(request: TradeRequest, session: Session = Depends(get_session)) -> dict:
    valuation = valuation_or_503(session, request.settings.to_settings())
    unknown = [pid for pid in (*request.give, *request.get) if pid not in valuation.by_id]
    if unknown:
        raise HTTPException(422, f"No {valuation.season} value for player id(s) {unknown}; "
                                 "only QB/RB/WR/TE with games this season can be traded.")
    try:
        evaluation = evaluate_trade([valuation.by_id[p] for p in request.give],
                                    [valuation.by_id[p] for p in request.get],
                                    valuation.values, valuation.weeks_remaining)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return serialize.trade(evaluation, valuation.weeks_remaining)


@router.get("/insights/buy-low")
def buy_low(position: Position | None = None, limit: int = Query(10, ge=1, le=50),
            league: LeagueSettings = Depends(league_params), session: Session = Depends(get_session)) -> dict:
    valuation = valuation_or_503(session, league)
    return serialize.insights(valuation, find_insights(valuation, "buy_low", position, limit))


@router.get("/insights/sell-high")
def sell_high(position: Position | None = None, limit: int = Query(10, ge=1, le=50),
              league: LeagueSettings = Depends(league_params), session: Session = Depends(get_session)) -> dict:
    valuation = valuation_or_503(session, league)
    return serialize.insights(valuation, find_insights(valuation, "sell_high", position, limit))


class TradeFindRequest(BaseModel):
    my_players: list[int] = Field(..., min_length=1, max_length=10)
    want_position: Position | None = None
    limit: int = Field(10, ge=1, le=25)
    settings: LeagueBody = LeagueBody()


@router.post("/trade/find")
def trade_find(request: TradeFindRequest, session: Session = Depends(get_session)) -> dict:
    """Offers the other manager should accept: fair or slight-edge 1-for-1 and 2-for-1 deals."""
    valuation = valuation_or_503(session, request.settings.to_settings())
    offers = find_trades(valuation, request.my_players, request.want_position, request.limit)
    return {"weeks_remaining": valuation.weeks_remaining,
            "results": [serialize.trade_offer(o, valuation.weeks_remaining) for o in offers]}
