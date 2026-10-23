"""Shared request dependencies and request bodies."""
from typing import Literal

from fastapi import HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from services.league import LeagueSettings
from services.valuation import NoDataError, Valuation, build_valuation

Scoring = Literal["ppr", "half", "std"]
Position = Literal["QB", "RB", "WR", "TE"]


class LeagueBody(BaseModel):
    scoring: Scoring = "ppr"
    teams: int = Field(12, ge=4, le=20)
    superflex: bool = False
    te_premium: float = Field(0.0, ge=0.0, le=1.0)

    def to_settings(self) -> LeagueSettings:
        return LeagueSettings(scoring=self.scoring, teams=self.teams, superflex=self.superflex,
                              te_premium=self.te_premium)


def league_params(
    scoring: Scoring = Query("ppr", description="Scoring format"),
    teams: int = Query(12, ge=4, le=20, description="Teams in the league"),
    superflex: bool = Query(False, description="Adds one QB/RB/WR/TE slot"),
    te_premium: float = Query(0.0, ge=0.0, le=1.0, description="Extra points per TE reception"),
) -> LeagueSettings:
    return LeagueBody(scoring=scoring, teams=teams, superflex=superflex, te_premium=te_premium).to_settings()


def valuation_or_503(session: Session, league: LeagueSettings, season: int | None = None) -> Valuation:
    try:
        return build_valuation(session, league, season)
    except NoDataError as exc:
        raise HTTPException(503, str(exc)) from exc
