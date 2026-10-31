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
from services.trade_finder import find_trades, market_percent

router = APIRouter(prefix="/api")


class TradeRequest(BaseModel):
    give: list[int] = Field(..., min_length=1, max_length=10)
    get: list[int] = Field(..., min_length=1, max_length=10)
    settings: LeagueBody = LeagueBody()
    league_id: int | None = Field(None, ge=1)   # score it on your real league's rosters and settings

    @field_validator("give", "get")
    @classmethod
    def no_duplicates(cls, ids: list[int]) -> list[int]:
        if len(set(ids)) != len(ids):
            raise ValueError("a player is listed more than once on the same side")
        return ids


@router.post("/trade/evaluate")
def trade_evaluate(request: TradeRequest, session: Session = Depends(get_session)) -> dict:
    """Value verdict for a trade; with league_id, also both real lineups' gains in that league."""
    settings = request.settings.to_settings()
    if request.league_id is not None:
        settings = _league_settings_or_404(session, request.league_id)
    valuation = valuation_or_503(session, settings)
    unknown = [pid for pid in (*request.give, *request.get) if pid not in valuation.by_id]
    if unknown:
        raise HTTPException(422, f"No {valuation.season} value for player id(s) {unknown}; "
                                 "only QB/RB/WR/TE with games this season can be traded.")
    impact, pool = None, valuation.values
    if request.league_id is not None:
        from services.league_trades import trade_impact
        from services.valuation import NoDataError

        try:
            impact = trade_impact(session, request.league_id, request.give, request.get)
        except NoDataError as exc:
            raise HTTPException(503, str(exc)) from exc
        pool_ids = impact.pop("pool_ids")
        pool = [v for v in valuation.values if v.player_id in pool_ids]      # only the two rosters
        impact = {**impact, **{key: serialize.player_ref(impact[key]) if impact[key] else None
                               for key in ("their_drop", "your_add")}}
    give, get = [valuation.by_id[p] for p in request.give], [valuation.by_id[p] for p in request.get]
    try:
        evaluation = evaluate_trade(give, get, pool, valuation.weeks_remaining)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {**serialize.trade(evaluation, valuation.weeks_remaining, market_percent(give, get, valuation)),
            "lineup": impact}


def _league_settings_or_404(session: Session, league_id: int) -> LeagueSettings:
    from services import my_team as mt

    try:
        league, _ = mt.find_league(session, league_id)
    except mt.LeagueNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return mt.league_settings(league)


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
